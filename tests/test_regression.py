import time
import hashlib
import unittest
from server.api import verify_token_or_hash
from server.prompt import extract_files
from copilot.client import CopilotClient

class TestRegression(unittest.TestCase):
    def test_auth_sha256(self):
        secret = "testsecret"
        
        # 1. Plaintext rejection
        self.assertFalse(verify_token_or_hash("mysecretpassword", secret))
        self.assertFalse(verify_token_or_hash("123456", secret))
        self.assertFalse(verify_token_or_hash(secret, secret))
        
        # 2. Valid timestamp (ms) + sha256
        now_ms = int(time.time() * 1000)
        valid_hash = hashlib.sha256(f"{now_ms}:{secret}".encode("utf-8")).hexdigest()
        valid_token = f"{now_ms}.{valid_hash}"
        self.assertTrue(verify_token_or_hash(valid_token, secret))
        
        # 3. Expired timestamp (> 300000 ms = 5 mins)
        old_ms = now_ms - 400000
        old_hash = hashlib.sha256(f"{old_ms}:{secret}".encode("utf-8")).hexdigest()
        old_token = f"{old_ms}.{old_hash}"
        self.assertFalse(verify_token_or_hash(old_token, secret))
        
        # 4. Future timestamp (> 300000 ms)
        future_ms = now_ms + 400000
        future_hash = hashlib.sha256(f"{future_ms}:{secret}".encode("utf-8")).hexdigest()
        future_token = f"{future_ms}.{future_hash}"
        self.assertFalse(verify_token_or_hash(future_token, secret))
        
        # 5. Invalid hash
        invalid_token = f"{now_ms}.0000000000000000000000000000000000000000000000000000000000000000"
        self.assertFalse(verify_token_or_hash(invalid_token, secret))

    def test_prompt_attachment_limit(self):
        # 4 files should raise ValueError("每轮对话最多允许上传3个附件。")
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "file_url", "file_url": {"url": "https://example.com/1.jpg"}},
                    {"type": "file_url", "file_url": {"url": "https://example.com/2.jpg"}},
                    {"type": "file_url", "file_url": {"url": "https://example.com/3.jpg"}},
                    {"type": "file_url", "file_url": {"url": "https://example.com/4.jpg"}},
                ]
            }
        ]
        with self.assertRaises(ValueError) as ctx:
            extract_files(messages)
        self.assertEqual(str(ctx.exception), "每轮对话最多允许上传3个附件。")

    def test_driver_attachment_limit(self):
        client = CopilotClient(session_dir="session")
        attachments = [
            {"file_name": f"{i}.txt", "mime_type": "text/plain", "data": b"test"} for i in range(4)
        ]
        with self.assertRaises(ValueError) as ctx:
            client.chat("test", e5_attachments=attachments)
        self.assertEqual(str(ctx.exception), "每轮对话最多允许上传3个附件。")

if __name__ == "__main__":
    unittest.main()
