import os
import sys
import time

# Ensure project root is on sys.path
sys.path.insert(0, os.path.abspath("."))

from copilot.client import CopilotClient

def test_real_image_upload():
    image_path = r"C:\Users\HengS\.antigravity-ide\extensions\anthropic.claude-code-2.1.202-win32-x64\resources\HighlightText.jpg"
    print("=== Starting Real Image Upload Test ===")
    print(f"Target image: {image_path}")
    
    if not os.path.exists(image_path):
        print(f"[Error] Image file not found at: {image_path}")
        return

    with open(image_path, "rb") as f:
        img_bytes = f.read()
    
    print(f"Loaded image: {len(img_bytes)} bytes")
    
    client = CopilotClient()
    print("Initiating CopilotClient stream with image attachment...")
    
    attachments = [{
        "file_name": os.path.basename(image_path),
        "mime_type": "image/jpeg",
        "data": img_bytes
    }]
    
    try:
        stream = client.stream(
            prompt="What is shown in this image? Please answer briefly in Chinese.",
            attachments=attachments
        )
        print("Waiting for response chunks...")
        for chunk in stream:
            if isinstance(chunk, str):
                print(chunk, end="", flush=True)
            else:
                print(f"\n[Received Non-Text Chunk]: {chunk}")
        print("\n\n=== Upload and Chat Completed Successfully! ===")
    except Exception as e:
        print(f"\n[Upload/Chat Failed] Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_real_image_upload()
