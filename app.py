from flask import Flask, request, jsonify
import os
import requests
import tempfile
import base64
import logging
import threading
import subprocess
from datetime import datetime
from google.cloud import storage

app = Flask(__name__)

LOG_DIR = "/app/logs"
os.makedirs(LOG_DIR, exist_ok=True)
START_TIME = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
LOG_FILENAME = os.path.join(LOG_DIR, f"log_{START_TIME}.txt")

storage_client = storage.Client()
BUCKET_NAME= os.environ.get('BUCKET_NAME')
if not BUCKET_NAME:
        raise RuntimeError("Error: bucket-name environment variable not set")

bucket = storage_client.bucket(BUCKET_NAME)


def log_to_gcs():
    """Upload the current log file to GCS with a timestamped name."""
    try:
        if os.path.exists(LOG_FILENAME):
            with open(LOG_FILENAME, 'r') as f:
                content = f.read()
            # Use the timestamped filename for upload
            gcs_filename = f"{START_TIME}.txt"
            blob = bucket.blob(gcs_filename)
            blob.upload_from_string(content)
            print(f"Uploaded {gcs_filename} to GCS.")
        else:
            print("Log file doesn't exist yet.")
    except Exception as e:
        print("Error uploading log to GCS:", e)

def log(message):
    """Prints message and writes to the timestamped log file."""
    print(message)
    try:
        with open(LOG_FILENAME, 'a') as f:
            f.write(message + "\n")
    except Exception as e:
        print("Error writing to log file:", e)


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
    thread = threading.Thread(target=handle_event_async, args=(payload, event_type))
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
        "Analyze the following Python code and generate comprehensive Pytest test functions for it. "
        "Create test cases that cover different scenarios including edge cases, valid inputs, invalid inputs, and error conditions. "
        "Use the pytest framework and include appropriate assertions. "
        "If the code contains functions, test each function separately. "
        "If the code contains classes, test the class methods and initialization. "
        "Test importability: avoid top-level execution on import; prefer importing by name and calling functions in tests. "
        "If the code uses input() or prints at import time, wrap interactive logic under a name == 'main' guard or expose a function to call in tests; show how to test interactive IO using monkeypatch or fixtures. "
        "Aim for deterministic tests: mock or patch any randomness, time, network, filesystem, or environment dependencies. "
        "Use parameterized tests where appropriate to cover multiple inputs. "
        "Structure tests with clear, named test functions and, if applicable, a small set of class-based tests for initialization and methods. "
        "Return only the test code, do not explain. "
        "Source code:\n{source_code_str}\n"
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

def upload_report_to_gcs(report_path, blob_name=None):
    """Upload a local test report (JUnit XML) to GCS."""
    if not os.path.exists(report_path):
        log(f"Report path not found: {report_path}")
        return
    if not BUCKET_NAME:
        log("BUCKET_NAME environment variable is not set")
        return
    if not blob_name:
        blob_name = f"reports/{START_TIME}-{os.path.basename(report_path)}"
    try:
        bucket_local = storage_client.bucket(BUCKET_NAME)
        blob = bucket_local.blob(blob_name)
        with open(report_path, 'rb') as f:
            blob.upload_from_file(f)
        log(f"Uploaded test report to GCS: {blob_name}")
    except Exception as e:
        log(f"Error uploading report to GCS: {e}")

def run_tests_for_test_file(test_filename):
    """Run pytest for a single test file and upload its JUnit XML report to GCS."""
    test_filepath = os.path.join("tests", test_filename)
    if not os.path.exists(test_filepath):
        log(f"Test file not found: {test_filepath}")
        return

    # Ensure reports dir exists
    os.makedirs("reports", exist_ok=True)

    # Output report path (JUnit XML)
    report_filename = f"{os.path.splitext(test_filename)[0]}_report.xml"
    report_path = os.path.join("reports", report_filename)

    # Run pytest for this test file and generate JUnit XML
    cmd = ["pytest", "-q", f"{test_filepath}", f"--junitxml={report_path}"]
    log(f"Running tests: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.stdout:
            log(result.stdout)
        if result.stderr:
            log(result.stderr)
        log(f"pytest finished with exit code: {result.returncode}")

        # Upload report to GCS
        blob_name = f"reports/{START_TIME}-{report_filename}"
        upload_report_to_gcs(report_path, blob_name=blob_name)
    except Exception as e:
        log(f"Error while running tests for {test_filename}: {e}")


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

                    local_module_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.path.basename(f))
                    try:
                        with open(local_module_path, 'w', encoding='utf-8') as lm:
                            lm.write(source_content)
                        log(f"Copied source to local module for testing: {local_module_path}")
                    except Exception as e:
                        log(f"Error writing local module for testing: {e}")

                    # Generate test code
                    test_code = generate_tests_for_code(source_content)
                    if not test_code:
                        log(f"No test code generated for {f}")
                        continue

                    # Save test code to local file temporarily
                    module_name = os.path.splitext(os.path.basename(f))[0]
                    test_filename = f"{module_name}_test.py"
                    test_dir = "tests"
                    os.makedirs(test_dir, exist_ok=True)
                    test_filepath = os.path.join(test_dir, test_filename)
                    with open(test_filepath, 'w', encoding='utf-8') as tf:
                        tf.write("import pytest\n")
                        tf.write(f"import sys\nimport os\n")
                        tf.write(f"sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))\n")
                        # tf.write(f"from {os.path.splitext(os.path.basename(f))[0]} import *\n\n")
                        tf.write(f"from {module_name} import *\n\n")
                        tf.write(test_code)
                    # Upload test file to GitHub repo
                    create_or_update_github_file(owner_repo, test_filepath, open(test_filepath, 'r').read())
                    # Run tests for this test file and upload the report
                    test_filename_only = test_filename  # e.g., file_test.py
                    run_tests_for_test_file(test_filename_only)
        else:
            log("No changed Python files detected.")
    elif event_type == 'pull_request':
        log("Pull request event detected.")
    else:
        log(f"Unhandled event: {event_type}")
    log_to_gcs()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', '8080'))
    log("Starting Flask app.")
    app.run(host='0.0.0.0', port=port)