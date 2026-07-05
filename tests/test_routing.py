"""Automated tests for Dynamic Model Routing & Agent Registry in E5 Copilot."""

import unittest
from copilot.agent_registry import get_agent_config, AGENT_REGISTRY, DEFAULT_E5_OPTIONS_SETS
from server.api import list_models


class TestAgentRegistryAndRouting(unittest.TestCase):

    def test_default_copilot_routing(self):
        cfg = get_agent_config("copilot")
        self.assertEqual(cfg["mode"], "Magic")
        self.assertIsNone(cfg["gptId"])
        self.assertEqual(cfg["optionsSets"], DEFAULT_E5_OPTIONS_SETS)

    def test_anthropic_claude_routing(self):
        cfg = get_agent_config("anthropic")
        self.assertEqual(cfg["mode"], "Magic")
        self.assertEqual(cfg["gptId"], "P_bbef1ffa-25db-4bb9-870d-afb22f41a4e9")
        self.assertIn("rich_responses", cfg["optionsSets"])

    def test_reasoning_mode_routing(self):
        cfg = get_agent_config("reasoning")
        self.assertEqual(cfg["mode"], "Reasoning")
        self.assertIsNone(cfg["gptId"])
        self.assertIn("Agt_bizchat_enableGpt5ForHelix", cfg["optionsSets"])

    def test_m365_agent_routing(self):
        for agent_name in ["word", "excel", "powerpoint", "designer", "pages"]:
            cfg = get_agent_config(agent_name)
            self.assertEqual(cfg["mode"], "Magic")
            self.assertEqual(cfg["gptId"], agent_name)

    def test_custom_plugin_id_routing(self):
        custom_id = "P_99999999-aaaa-bbbb-cccc-111122223333"
        cfg = get_agent_config(custom_id)
        self.assertEqual(cfg["mode"], "Magic")
        self.assertEqual(cfg["gptId"], custom_id)
        self.assertEqual(cfg["optionsSets"], DEFAULT_E5_OPTIONS_SETS)

    def test_case_insensitivity_and_fallback(self):
        cfg = get_agent_config("Claude-Opus-4.8")
        self.assertEqual(cfg["gptId"], "P_bbef1ffa-25db-4bb9-870d-afb22f41a4e9")

        cfg_fable = get_agent_config("claude-fable-5")
        self.assertEqual(cfg_fable["gptId"], "P_bbef1ffa-25db-4bb9-870d-afb22f41a4e9")

        cfg_auto = get_agent_config("自动")
        self.assertEqual(cfg_auto["mode"], "Magic")

        cfg_fast = get_agent_config("快速答复")
        self.assertEqual(cfg_fast["mode"], "Chat")

        cfg_think = get_agent_config("深度思考")
        self.assertEqual(cfg_think["mode"], "Reasoning")
        self.assertIn("Agt_bizchat_enableGpt5ForHelix", cfg_think["optionsSets"])

        cfg_55_fast = get_agent_config("gpt 5.5 快速响应")
        self.assertEqual(cfg_55_fast["mode"], "Gpt_5_5_Chat")

        cfg_55_think = get_agent_config("gpt 5.5 深度思考")
        self.assertEqual(cfg_55_think["mode"], "Gpt_5_5_Reasoning")

        # Fuzzy prefix matching test
        cfg_fuzzy_claude = get_agent_config("claude-fable-5-latest")
        self.assertEqual(cfg_fuzzy_claude["gptId"], "P_bbef1ffa-25db-4bb9-870d-afb22f41a4e9")

        # Unknown model should gracefully fallback to default copilot
        cfg_unknown = get_agent_config("some-nonexistent-model")
        self.assertIsNone(cfg_unknown["gptId"])
        self.assertEqual(cfg_unknown["mode"], "Magic")

    def test_server_list_models_endpoint(self):
        resp = list_models()
        self.assertEqual(resp["object"], "list")
        model_ids = [m["id"] for m in resp["data"]]
        self.assertIn("copilot", model_ids)
        self.assertIn("anthropic", model_ids)
        self.assertIn("reasoning", model_ids)
        self.assertIn("word", model_ids)
        self.assertIn("excel", model_ids)
        self.assertEqual(len(model_ids), len(AGENT_REGISTRY))


if __name__ == "__main__":
    unittest.main()
