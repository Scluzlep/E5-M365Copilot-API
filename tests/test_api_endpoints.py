"""Test FastAPI endpoints directly using TestClient."""
import os
import unittest
from fastapi.testclient import TestClient

os.environ["WEB_AUTH_PASSWORD"] = "testpassword123"

from server.api import app

client = TestClient(app)

class TestApiEndpoints(unittest.TestCase):
    def test_list_models_endpoint(self):
        resp = client.get("/v1/models")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data.get("object"), "list")
        models = data.get("data", [])
        self.assertGreaterEqual(len(models), 40)
        ids = [m["id"] for m in models]
        self.assertIn("grok-4.5", ids)
        self.assertIn("grok-4.5-持续", ids)
        self.assertIn("gpt-6_Chat", ids)
        self.assertIn("gpt-6", ids)
        self.assertIn("claude-sonnet-4-6", ids)

    def test_pkce_start_endpoint_requires_auth(self):
        # Without admin auth header -> 401
        resp = client.post("/api/pkce/start", json={"session_name": "test_sess"})
        self.assertEqual(resp.status_code, 401)

    def test_pkce_start_with_admin_auth(self):
        import time, hashlib
        ts = int(time.time() * 1000)
        sig = hashlib.sha256(f"{ts}:testpassword123".encode("utf-8")).hexdigest()
        token = f"{ts}.{sig}"
        
        resp = client.post(
            "/api/pkce/start",
            json={"session_name": "sess_test_pkce"},
            headers={"X-Admin-Auth": token}
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body.get("success"))
        self.assertIn("authorize_url", body.get("data", {}))


if __name__ == "__main__":
    unittest.main()
