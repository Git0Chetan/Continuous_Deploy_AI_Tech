from flask import Flask, request, jsonify
import os
import requests
import tempfile
import base64
import logging
import threading
from datetime import datetime
from google.cloud import storage

app = Flask(__name__)

LOG_DIR = "/app/logs"
LOG_FILE_PATH = os.path.join(LOG_DIR, "app.log")
os.makedirs(LOG_DIR, exist_ok=True)

storage_client = storage.Client()
BUCKET_NAME= os.environ.get('BUCKET_NAME')
if not BUCKET_NAME:
        raise RuntimeError("Error: bucket-name environment variable not set")

bucket = storage_client.bucket(BUCKET_NAME)


def log_to_gcs(message):
    # Append to local log file
    try:
        with open(LOG_FILE_PATH, 'a') as f:
            f.write(message + "\n")
    except Exception as e:
        print("Error writing to local log file:", e)

    # Upload entire log to GCS
    try:
        if os.path.exists(LOG_FILE_PATH):
            with open(LOG_FILE_PATH, 'r') as f:
                content = f.read()
            blob = bucket.blob('app.log')  # static filename
            blob.upload_from_string(content)
            print("Logs uploaded to GCS")
        else:
            print("Log file not found for upload.")
    except Exception as e:
        print("Error uploading logs to GCS:", e)


def log(message):
    """Print and save logs."""
    print(message)
    log_to_gcs(message)

@app.get("/")
def health_check():
    message = "Health check triggered."
    log(message)
    return jsonify({'status': 'healthy'}), 200



def handle_event_async(payload, event_type):
    """Process webhook event asynchronously."""
    try:
        handle_event(payload, event_type)
        log("Background processing completed successfully.")
    except Exception as e:
        log(f"Error in background processing: {e}")


@app.route('/webhook', methods=['POST'])
def github_webhook():
    payload = request.json
    event_type = request.headers.get('X-GitHub-Event', 'No Event Header')
    log(f"Webhook triggered for event: {event_type}")

    # Start async processing to avoid timeout
    thread = Thread(target=handle_event_async, args=(payload, event_type))
    thread.start()

    log("Started background processing for event.")
    return jsonify({'status': 'processing'}), 200


def is_tag_event(payload):
    ref = payload.get('ref', '')
    result = ref.startswith('refs/tags/')
    log(f"is_tag_event: ref={ref} result={result}")
    return result

def get_tag_name(payload):
    ref = payload.get('ref', '')
    if ref.startswith('refs/tags/'):
        tag_name = ref.replace('refs/tags/', '')
        log(f"Extracted tag: {tag_name}")
        return tag_name
    return None

def get_changed_files(payload):
    if 'before' in payload and 'after' in payload:
        ref = payload['after']
        owner_repo = payload['repository']['full_name']
        GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN')
        if not GITHUB_TOKEN:
            log("GITHUB_TOKEN environment variable not set")
            return []
        url = f"https://api.github.com/repos/{owner_repo}/commits/{ref}"
        headers = {'Authorization': f'token {GITHUB_TOKEN}'}
        response = requests.get(url, headers=headers)
        log(f"Fetching commit details: {url} Status: {response.status_code}")
        if response.status_code == 200:
            commit_info = response.json()
            files = commit_info.get('files', [])
            filenames = [file['filename'] for file in files]
            log(f"Changed files: {filenames}")
            return filenames
        else:
            log(f"Failed to fetch commit details: {response.status_code}")
            return []
    else:
        log("Payload missing 'before' or 'after'")
        return []

def fetch_file_content_in_repo(owner_repo, filepath, branch='main'):
    GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN')
    url = f"https://api.github.com/repos/{owner_repo}/contents/{filepath}?ref={branch}"
    headers = {'Authorization': f'token {GITHUB_TOKEN}'}
    response = requests.get(url, headers=headers)
    log(f"Fetching file content: {url} Status: {response.status_code}")
    if response.status_code == 200:
        json_data = response.json()
        content_b64 = json_data['content']
        content = base64.b64decode(content_b64).decode('utf-8')
        log(f"Fetched content for {filepath}")
        return content
    else:
        log(f"Failed to fetch file: {filepath}")
        return None

def create_or_update_github_file(owner_repo, filepath, content, branch='main'):
    GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN')
    url = f"https://api.github.com/repos/{owner_repo}/contents/{filepath}"
    headers = {'Authorization': f'token {GITHUB_TOKEN}'}

    # Check if file exists to get sha
    response = requests.get(url, headers=headers)
    data = {
        "message": f"Add or update {filepath}",
        "content": base64.b64encode(content.encode()).decode(),
        "branch": branch
    }
    if response.status_code == 200:
        sha = response.json().get('sha')
        data['sha'] = sha
        log(f"Existing file sha found: {sha}")
    response_put = requests.put(url, headers=headers, json=data)
    if response_put.status_code in (200, 201):
        log(f"Successfully uploaded {filepath}")
    else:
        log(f"Failed to upload {filepath}: {response_put.status_code} {response_put.text}")

def generate_tests_for_code(source_code_str):
    import google.generativeai as genai
    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        log("Error: OPENAI_API_KEY environment variable not set")
        return ""
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-1.5-flash")
    prompt = (
        "You are a Python test automation engineer. "
        "Analyze the following Python code and generate comprehensive pytest test functions for it. "
        "Create test cases covering different scenarios, edge cases, valid and invalid inputs, and error conditions. "
        "Use the pytest framework, include appropriate assertions, and test functions and classes as applicable.\n\n"
        f"Source code:\n{source_code_str}\n"
    )
    response = model.generate_content(prompt)
    test_code = response.text.strip()

    # Remove code block delimiters if present
    if test_code.startswith("```python"):
        test_code = test_code[9:]
    if test_code.endswith("```"):
        test_code = test_code[:-3]
    log("Generated test code for source.")
    return test_code

def handle_event(payload, event_type):
    if event_type == 'push':
        # Check for tag event
        if is_tag_event(payload):
            tag_name = get_tag_name(payload)
            log(f"Tag detected: {tag_name}")
            if tag_name == 'yes_test':
                log("Test cases are required due to 'yes_test' tag.")
        else:
            log("No tag detected.")

        changed_files = get_changed_files(payload)
        owner_repo = payload['repository']['full_name']
        branch = payload['ref'].split("/")[-1]  # e.g., refs/heads/main -> main
        log(f"Processing branch: {branch}")

        if changed_files:
            for f in changed_files:
                if f.endswith('.py') and not f.endswith('_test.py'):
                    log(f"Processing file: {f}")
                    source_content = fetch_file_content_in_repo(owner_repo, f, branch)
                    if source_content is None:
                        log(f"Could not fetch source for {f}")
                        continue

                    # Generate test code
                    test_code = generate_tests_for_code(source_content)
                    if not test_code:
                        log(f"No test code generated for {f}")
                        continue

                    # Save test code to local file temporarily
                    test_filename = f"{os.path.splitext(os.path.basename(f))[0]}_test.py"
                    test_dir = "tests"
                    os.makedirs(test_dir, exist_ok=True)
                    test_filepath = os.path.join(test_dir, test_filename)
                    with open(test_filepath, 'w', encoding='utf-8') as tf:
                        tf.write("import pytest\n")
                        tf.write(f"import sys\nimport os\n")
                        tf.write(f"sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))\n")
                        tf.write(f"from {os.path.splitext(os.path.basename(f))[0]} import *\n\n")
                        tf.write(test_code)
                    # Upload test file to GitHub repo
                    create_or_update_github_file(owner_repo, test_filepath, open(test_filepath, 'r').read())
        else:
            log("No changed Python files detected.")
    elif event_type == 'pull_request':
        log("Pull request event detected.")
    else:
        log(f"Unhandled event: {event_type}")

if __name__ == '__main__':
    port = int(os.environ.get('PORT', '8080'))
    log("Starting Flask app.")
    app.run(host='0.0.0.0', port=port)