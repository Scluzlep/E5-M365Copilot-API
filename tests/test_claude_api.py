import json
import urllib.request
import sys

def test_non_streaming():
    print("Testing Non-Streaming...")
    payload = {
        "model": "claude-3-opus-20240229",
        "system": "You are a helpful assistant.",
        "messages": [
            {"role": "user", "content": "What is 2+2? Only output the answer, no other text."}
        ],
        "stream": False
    }
    req = urllib.request.Request("http://127.0.0.1:8000/v1/messages", data=json.dumps(payload).encode('utf-8'), headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req) as response:
            print(response.getcode())
            print(json.dumps(json.loads(response.read().decode('utf-8')), indent=2))
    except urllib.error.HTTPError as e:
        print(e.code)
        print(e.read().decode('utf-8'))
        
def test_streaming():
    print("\nTesting Streaming...")
    payload = {
        "model": "claude-3-opus-20240229",
        "messages": [
            {"role": "user", "content": "What is 3+3? Only output the answer, no other text."}
        ],
        "stream": True
    }
    req = urllib.request.Request("http://127.0.0.1:8000/v1/messages", data=json.dumps(payload).encode('utf-8'), headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req) as response:
            for line in response:
                line_str = line.decode('utf-8').strip()
                if line_str:
                    print(line_str)
    except urllib.error.HTTPError as e:
        print(e.code)
        print(e.read().decode('utf-8'))

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "stream":
        test_streaming()
    else:
        test_non_streaming()
