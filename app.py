from flask import Flask, request, jsonify
import os
import requests

app = Flask(__name__)git

@app.get("/")
def health_check():
    return jsonify({'status': 'healthy'}), 200

@app.route('/webhook', methods=['POST'])
def github_webhook():
    payload = request.json
    # Log payload for debugging
    print("Received event:", request.headers.get('X-GitHub-Event'))
    print("Payload:", payload)

    # Trigger your decision module here
    handle_event(payload)

    return jsonify({'status': 'ok'}), 200

def is_tag_event(payload):
    ref = payload.get('ref', '')
    return ref.startswith('refs/tags/')

def get_tag_name(payload):
    ref = payload.get('ref', '')
    if ref.startswith('refs/tags/'):
        return ref.replace('refs/tags/', '')
    return None

def get_changed_files(payload):
    # For push events
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
            print(f"Failed to fetch commit details: {r.status_code}")
            return []
    # Extend for PR events if necessary
    return []

def requires_test_update(files):
    critical_paths = ['payment', 'transaction', 'api']
    for file in files:
        for path in critical_paths:
            if path in file:
                return True
    return False

def handle_event(payload):
    event_type = request.headers.get('X-GitHub-Event')
    
    if event_type == 'push':
        if is_tag_event(payload):
            tag_name = get_tag_name(payload)
            print(f"Tag detected: {tag_name}")
            if tag_name == 'yes_test':
                print("Test cases are required due to 'yes_test' tag.")
                # Trigger test case generation here
                return
        # If not a relevant tag, check changed files
        changed_files = get_changed_files(payload)
        if requires_test_update(changed_files):
            print("Changes detected requiring new tests.")
        else:
            print("No testing updates required.")
    elif event_type == 'pull_request':
        print("Pull request event detected.")
        # Handle PR events if needed

if __name__ == '__main__':
    port = int(os.environ.get('PORT', '8080'))
    app.run(host='0.0.0.0', port=port)