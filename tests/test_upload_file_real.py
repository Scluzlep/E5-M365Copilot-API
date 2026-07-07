import os
import sys
import mimetypes

# Ensure project root is on sys.path
sys.path.insert(0, os.path.abspath("."))

from copilot.client import CopilotClient

def test_real_file_upload():
    file_path = r"E:\Downloads\大数据专业竞赛实训报告.md"
    print("=== Starting Real File Upload Test ===")
    print(f"Target file: {file_path}")
    
    if not os.path.exists(file_path):
        print(f"[Error] File not found at: {file_path}")
        return

    with open(file_path, "rb") as f:
        file_bytes = f.read()
    
    print(f"Loaded file: {len(file_bytes)} bytes")
    
    # Determine mime type
    mime_type, _ = mimetypes.guess_type(file_path)
    if not mime_type:
        mime_type = "text/plain"
    print(f"Detected MIME type: {mime_type}")
    
    client = CopilotClient()
    print("Initiating CopilotClient stream with file attachment...")
    
    attachments = [{
        "file_name": os.path.basename(file_path),
        "mime_type": mime_type,
        "data": file_bytes
    }]
    
    try:
        stream = client.stream(
            prompt="请用一两句话简要总结这个报告的主要内容。",
            attachments=attachments
        )
        print("Waiting for response chunks...")
        for chunk in stream:
            if isinstance(chunk, str):
                print(chunk, end="", flush=True)
            else:
                print(f"\n[Received Non-Text Chunk]: {chunk}")
        print("\n\n=== File Upload and Chat Completed Successfully! ===")
    except Exception as e:
        print(f"\n[Upload/Chat Failed] Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_real_file_upload()
