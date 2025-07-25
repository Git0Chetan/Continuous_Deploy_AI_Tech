from flask import Flask, request, jsonify
import os
import requests
import tempfile
import base64
import logging
from datetime import datetime
from google.cloud import storage

app = Flask(__name__)

storage_client = storage.Client()
BUCKET_NAME= os.environ.get('BUCKET_NAME')
    if not BUCKET_NAME:
        log("Error: bucket-name environment variable not set")

bucket = storage_client.bucket(BUCKET_NAME)

# Set up logs directory and log file
# LOG_DIR = "/app/logs"
# os.makedirs(LOG_DIR, exist_ok=True)
# LOG_FILE = os.path.join(LOG_DIR, "app.log")

# Configure logging
# logging.basicConfig(
#     level=logging.INFO,
#     format='%(asctime)s %(levelname)s: %(message)s',
#     handlers=[
#         logging.FileHandler(LOG_FILE),
#         logging.StreamHandler()  # ensures logs show up in Cloud Run logs
#     ]
# )

def get_log_filename():
    # Create filename like logs_YYYYMMDD-HHMMSS.txt
    timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    return f"logs_{timestamp}.txt"

def log_to_gcs(message):
    filename = get_log_filename()
    blob = bucket.blob(filename)
    try:
        # Download existing contents if exists
        if blob.exists():
            existing_content = blob.download_as_text()
        else:
            existing_content = ""
    except Exception:
        existing_content = ""
    # Append new message
    new_content = existing_content + message + "\n"
    blob.upload_from_string(new_content)

def log(message):
    print(message)  # still print locally and in cloud logs
    log_to_gcs(message)

@app.get("/")
def health_check():
    log("System is working in good condition - chetan")
    return jsonify({'status': 'healthy'}), 200

@app.route('/webhook', methods=['POST'])
def github_webhook():
    payload = request.json
    event_type = request.headers.get('X-GitHub-Event', 'No Event Header')
    log(f"Received event: {event_type}")
    try:
        handle_event(payload, event_type)
    except Exception:
        # Log any unexpected errors
        logging.exception("Error handling webhook")
        return jsonify({'error': 'Internal server error'}), 500
    return "Event processed", 200



def is_tag_event(payload):
    ref = payload.get('ref', '')
    return ref.startswith('refs/tags/')

def get_tag_name(payload):
    ref = payload.get('ref', '')
    if ref.startswith('refs/tags/'):
        return ref.replace('refs/tags/', '')
    return None

def get_changed_files(payload):
    if 'before' in payload and 'after' in payload:
        ref = payload['after']
        owner_repo = payload['repository']['full_name']
        GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN')
        if not GITHUB_TOKEN:
            raise RuntimeError("GITHUB_TOKEN environment variable not set")
        url = f"https://api.github.com/repos/{owner_repo}/commits/{ref}"
        headers = {'Authorization': f'token {GITHUB_TOKEN}'}
        r = requests.get(url, headers=headers)
        if r.status_code == 200:
            commit_info = r.json()
            files = commit_info.get('files', [])
            return [file['filename'] for file in files]
        else:
            log(f"Failed to fetch commit details: {r.status_code}")
            return []
    return []


def fetch_file_content_in_repo(owner_repo, filepath, branch='main'):
    GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN')
    url = f"https://api.github.com/repos/{owner_repo}/contents/{filepath}?ref={branch}"
    headers = {'Authorization': f'token {GITHUB_TOKEN}'}
    r = requests.get(url, headers=headers)
    if r.status_code == 200:
        json_data = r.json()
        content_b64 = json_data['content']
        return base64.b64decode(content_b64).decode('utf-8')
    return None


def create_or_update_github_file(owner_repo, filepath, content, branch='main'):
    GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN')
    url = f"https://api.github.com/repos/{owner_repo}/contents/{filepath}"
    headers = {'Authorization': f'token {GITHUB_TOKEN}'}
    # Check if file exists to get its sha
    r = requests.get(url, headers=headers)
    data = {
        "message": f"Add or update {filepath}",
        "content": base64.b64encode(content.encode()).decode(),
        "branch": branch
    }
    if r.status_code == 200:
        sha = r.json().get('sha')
        data['sha'] = sha
    r2 = requests.put(url, headers=headers, json=data)
    if r2.status_code in (200, 201):
        log(f"Successfully uploaded: {filepath}")
    else:
        log(f"Failed to upload {filepath}: {r2.status_code}, {r2.text}")


def generate_tests_for_code(source_code_str):
    import google.generativeai as genai
    import os
    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        log("Error: GOOGLE_API_KEY environment variable not set")
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
    return test_code


def handle_event(payload, event_type):
    if event_type == 'push':
        # Check for tag event
        if is_tag_event(payload):
            tag_name = get_tag_name(payload)
            log(f"Tag detected: {tag_name}")
            if tag_name == 'yes_test':
                log("Test cases are required due to 'yes_test' tag.")
                # Fall through to generate tests for modified files

        changed_files = get_changed_files(payload)
        owner_repo = payload['repository']['full_name']
        branch = payload['ref'].split("/")[-1]  # e.g., refs/heads/main -> main

        if changed_files:
            # For each modified Python file, generate tests and push
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

                    # Determine test filename
                    test_filename = f"{os.path.splitext(os.path.basename(f))[0]}_test.py"
                    test_dir = "tests"
                    os.makedirs(test_dir, exist_ok=True)
                    test_filepath = os.path.join(test_dir, test_filename)

                    # Write test code to file
                    with open(test_filepath, 'w', encoding='utf-8') as tf:
                        tf.write("import pytest\n")
                        tf.write(f"import sys\nimport os\n")
                        tf.write(f"sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))\n")
                        tf.write(f"from {os.path.splitext(os.path.basename(f))[0]} import *\n\n")
                        tf.write(test_code)

                    # Push test file to GitHub repository
                    owner_repo_name = owner_repo  # e.g., username/repo
                    create_or_update_github_file(owner_repo_name, test_filepath, open(test_filepath, 'r').read())
        else:
            log("No changed Python files detected.")
    elif event_type == 'pull_request':
        log("Pull request event detected.")
    else:
        log(f"Unhandled event type: {event_type}")

if __name__ == '__main__':
    port = int(os.environ.get('PORT', '8080'))
    app.run(host='0.0.0.0', port=port)