"""Automated tests for modern upgrades:
1. Atomic write & fsync durability
2. Tool hygiene deduplication
3. Updated M365 models registry (40 models, _Chat naming, -持续 persistence)
4. M365 Native App PKCE logic
5. SSE guard stream keepalive
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import unittest

from copilot.atomic_write import write_text_atomic
from copilot.tool_hygiene import deduplicate_tool_calls
from copilot.agent_registry import get_agent_config, get_all_models_data, BASE_TONE_DEFINITIONS
from copilot.pkce import make_verifier, code_challenge, extract_code_from_redirect_url, start_pkce_login
from server.sse_stream import async_sse_guard, SSE_KEEPALIVE_COMMENT, ANTHROPIC_PING


class TestModernUpgrades(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_atomic_write_durability(self):
        test_file = os.path.join(self.test_dir, "atomic_test.json")
        data = {"hello": "world", "num": 12345}
        text = json.dumps(data)
        write_text_atomic(test_file, text, durable=True)
        self.assertTrue(os.path.exists(test_file))
        with open(test_file, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        self.assertEqual(loaded, data)

    def test_tool_hygiene_deduplication(self):
        # Tool calls containing duplicate ID and duplicate name+args
        raw_tool_calls = [
            {"id": "call_1", "name": "get_weather", "arguments": {"city": "Beijing"}},
            {"id": "call_2", "name": "get_weather", "arguments": {"city": "Beijing"}},  # Duplicate signature!
            {"id": "call_1", "name": "different_tool", "arguments": {}},                 # Duplicate ID!
            {"id": "call_3", "name": "search_web", "arguments": {"query": "OpenAI"}},
        ]
        kept, reasons = deduplicate_tool_calls(raw_tool_calls, by_name_and_args=True)
        self.assertEqual(len(kept), 2)
        self.assertEqual(kept[0]["id"], "call_1")
        self.assertEqual(kept[1]["id"], "call_3")
        self.assertEqual(len(reasons), 2)

    def test_model_registry_and_naming_overhaul(self):
        # 1. Check all base tones use official _Chat and NOT obsolete _Quick
        for item in BASE_TONE_DEFINITIONS:
            tone = item["tone"]
            self.assertNotIn("Quick", tone, f"Obsolete _Quick naming detected in tone: {tone}")

        # 2. Check Grok 4.5
        grok_cfg = get_agent_config("grok-4.5")
        self.assertEqual(grok_cfg["mode"], "Grok_4_5")
        self.assertFalse(grok_cfg["persist_session"])

        # 3. Check GPT-6 Astra and Reasoning
        gpt6_chat = get_agent_config("gpt-6_Chat")
        self.assertEqual(gpt6_chat["mode"], "Gpt_6_Astra")

        gpt6_reasoning = get_agent_config("gpt-6")
        self.assertEqual(gpt6_reasoning["mode"], "Gpt_6_Reasoning")

        # 4. Check persist variant (-持续)
        gpt55_persist = get_agent_config("gpt-5.5_Chat-持续")
        self.assertEqual(gpt55_persist["mode"], "Gpt_5_5_Chat")
        self.assertTrue(gpt55_persist["persist_session"])

        # 5. Check all models returned in /v1/models
        all_models = get_all_models_data()
        self.assertGreaterEqual(len(all_models), 40)
        model_ids = {m["id"] for m in all_models}
        self.assertIn("grok-4.5", model_ids)
        self.assertIn("grok-4.5-持续", model_ids)
        self.assertIn("gpt-6_Chat", model_ids)
        self.assertIn("gpt-6_Chat-持续", model_ids)
        self.assertIn("claude-sonnet-4-6", model_ids)

    def test_pkce_generation_and_extraction(self):
        verifier = make_verifier()
        self.assertGreaterEqual(len(verifier), 43)
        challenge = code_challenge(verifier)
        self.assertTrue(bool(challenge))

        # Test code extraction
        fake_redirect_url = "https://login.microsoftonline.com/common/oauth2/nativeclient?code=M.C123_456_test_code&session_state=abc"
        code = extract_code_from_redirect_url(fake_redirect_url)
        self.assertEqual(code, "M.C123_456_test_code")

        # Test start_pkce_login
        start_res = start_pkce_login("test_sess", authority="common")
        self.assertIn("authorize_url", start_res)
        self.assertIn("client_id=c0ab8ce9-e9a0-42e7-b064-33d422df41f1", start_res["authorize_url"])
        self.assertIn("code_challenge=", start_res["authorize_url"])

    def test_sse_guard_stream(self):
        async def run_test():
            def sample_gen():
                yield "data: chunk1\n\n"
                yield "data: chunk2\n\n"

            collected = []
            async for chunk in async_sse_guard(sample_gen(), keepalive_interval=1.0, write_timeout=5.0):
                collected.append(chunk)

            self.assertEqual(collected, ["data: chunk1\n\n", "data: chunk2\n\n"])

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
