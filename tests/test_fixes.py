import sys
import os
import time

# Add root dir to path
sys.path.insert(0, os.path.abspath("."))

print("=== Starting Fixes Verification Test ===")

# 1. Test Token Masking
from copilot.utils import mask_token
test_str = "Failed to connect: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9... and access_token=secret_token_12345"
masked = mask_token(test_str)
print(f"[Test 1] Original: {test_str}")
print(f"[Test 1] Masked  : {masked}")
assert "secret_token_12345" not in masked, "access_token not masked!"
assert "eyJhbG" not in masked, "Bearer token not masked!"
assert "***" in masked, "Mask replacement *** not found!"
print("[Test 1] Token Masking: PASSED\n")

# 2. Test SSRF Protection
from server.prompt import is_safe_url, image_cache
print("[Test 2] Testing SSRF IP Blacklist...")
assert not is_safe_url("http://127.0.0.1/admin"), "Failed to block 127.0.0.1!"
assert not is_safe_url("http://localhost:8000/"), "Failed to block localhost!"
assert not is_safe_url("http://169.254.169.254/latest/meta-data/"), "Failed to block AWS metadata IP!"
assert not is_safe_url("http://10.1.2.3/internal"), "Failed to block private 10.x IP!"
assert not is_safe_url("http://192.168.1.1/router"), "Failed to block private 192.168.x IP!"
assert not is_safe_url("file:///etc/passwd"), "Failed to block file:// scheme!"
assert is_safe_url("https://www.microsoft.com"), "Blocked legitimate external URL!"

# Test get_or_fetch with SSRF url
res = image_cache.get_or_fetch("http://127.0.0.1:5900/secret.png")
assert res is None, "RemoteImageCache did not block SSRF URL!"
print("[Test 2] SSRF Protection & Image Cache: PASSED\n")

# 3. Test Compound Routing Isolation & Debounce Persistence
from server.router import router, ConversationState
from server.accounts import pool, SessionInstance
from server.schemas import ChatMessage

print("[Test 3] Testing Compound Routing & Isolation...")
# Setup mock pool keys and sessions
pool.api_keys["keyA"] = ["sessA"]
pool.api_keys["keyB"] = ["sessB"]
pool.sessions["sessA"] = SessionInstance("sessA")
pool.sessions["sessB"] = SessionInstance("sessB")

messages = [
    ChatMessage(role="user", content="Hello"),
    ChatMessage(role="assistant", content="Hi there!"),
    ChatMessage(role="user", content="How are you?")
]
history = messages[:-1]

# Save state for User A
head_hash_A = router._hash_messages(messages)
router.save_state(head_hash_A, "conv_user_A", "sessA", api_key="keyA")

# Verify User A routes to conv_user_A on sessA
cid_A, prompt_A, new_hash_A, sess_A = router.route("keyA", messages + [ChatMessage(role="user", content="Next")], None)
print(f"[Test 3] User A routing result: cid={cid_A}, sess={sess_A}")
assert cid_A == "conv_user_A", f"User A failed to match own conversation! Got {cid_A}"
assert sess_A == "sessA", f"User A failed to match own session! Got {sess_A}"

# Verify User B with identical history does NOT collide with User A!
cid_B, prompt_B, new_hash_B, sess_B = router.route("keyB", messages + [ChatMessage(role="user", content="Next")], None)
print(f"[Test 3] User B routing result: cid={cid_B}, sess={sess_B}")
assert cid_B is None, f"Crosstalk vulnerability! User B matched User A's conversation: {cid_B}"
assert sess_B is None, f"Crosstalk vulnerability! User B matched User A's session: {sess_B}"
print("[Test 3] Multi-tenant Compound Routing Isolation: PASSED\n")

print("[Test 4] Testing Debounce Disk Persistence...")
assert router._dirty is True, "Router dirty flag was not set after save_state!"
print("[Test 4] Waiting 2.5 seconds for async debounce timer to flush to disk...")
time.sleep(2.5)
assert router._dirty is False, "Router dirty flag was not cleared after timer fired!"
assert os.path.exists(router.persist_path), "conversations.json was not written to disk!"
print("[Test 4] Async Debounce Persistence: PASSED\n")

print("=== ALL VERIFICATION TESTS PASSED SUCCESSFULLY! ===")
