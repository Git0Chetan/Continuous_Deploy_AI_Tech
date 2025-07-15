from flask import Flask, request, jsonify

app = Flask(__name__)

@app.route('/webhook', methods=['POST'])
def github_webhook():
    payload = request.json
    # Log payload for debugging
    print("Received event:", request.headers.get('X-GitHub-Event'))
    print("Payload:", payload)

    # Trigger your decision module here (call function)
    handle_event(payload)

    return jsonify({'status': 'ok'}), 200

def handle_event(payload):
    event_type = request.headers.get('X-GitHub-Event')
    if event_type == 'push':
        print("Push event detected")
        # You can add specific logic here
    elif event_type == 'pull_request':
        print("Pull request event detected")
    # Further processing as needed

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)