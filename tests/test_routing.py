"""Automated tests for Dynamic Model Routing & Agent Registry in E5 Copilot."""

import unittest
from copilot.agent_registry import get_agent_config, AGENT_REGISTRY, DEFAULT_E5_OPTIONS_SETS
from server.api import list_models


class TestAgentRegistryAndRouting(unittest.TestCase):

    def test_default_auto_routing(self):
        cfg = get_agent_config("auto")
        self.assertEqual(cfg["mode"], "Magic")
        self.assertIsNone(cfg["gptId"])
        self.assertEqual(cfg["optionsSets"], DEFAULT_E5_OPTIONS_SETS)

        cfg_none = get_agent_config(None)
        self.assertEqual(cfg_none["mode"], "Magic")
        self.assertIsNone(cfg_none["gptId"])

    def test_native_claude_routing(self):
        cfg_sonnet = get_agent_config("claude-sonnet-4.6")
        self.assertEqual(cfg_sonnet["mode"], "Claude_Sonnet")
        self.assertIsNone(cfg_sonnet["gptId"])

        cfg_sonnet_think = get_agent_config("claude-sonnet-thinking")
        self.assertEqual(cfg_sonnet_think["mode"], "Claude_Sonnet_Reasoning")
        self.assertIsNone(cfg_sonnet_think["gptId"])
        self.assertIn("Agt_bizchat_enableGpt5ForHelix", cfg_sonnet_think["optionsSets"])

        cfg_opus = get_agent_config("claude-opus-4.8")
        self.assertEqual(cfg_opus["mode"], "Claude_Opus")
        self.assertIsNone(cfg_opus["gptId"])

        cfg_fable = get_agent_config("claude-fable-5")
        self.assertEqual(cfg_fable["mode"], "Claude_Fable")
        self.assertIsNone(cfg_fable["gptId"])

        cfg_mythos = get_agent_config("claude-mythos-5")
        self.assertEqual(cfg_mythos["mode"], "Claude_Mythos")
        self.assertIsNone(cfg_mythos["gptId"])

    def test_reasoning_mode_routing(self):
        cfg = get_agent_config("thinking")
        self.assertEqual(cfg["mode"], "Reasoning")
        self.assertIsNone(cfg["gptId"])
        self.assertIn("Agt_bizchat_enableGpt5ForHelix", cfg["optionsSets"])

        cfg_55 = get_agent_config("gpt-5.5-thinking")
        self.assertEqual(cfg_55["mode"], "Gpt_5_5_Reasoning")
        self.assertIn("Agt_bizchat_enableGpt5ForHelix", cfg_55["optionsSets"])

        cfg_56 = get_agent_config("gpt-5.6-thinking")
        self.assertEqual(cfg_56["mode"], "Gpt_5_6_Reasoning")

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
        self.assertEqual(cfg["mode"], "Claude_Opus")

        cfg_fable = get_agent_config("claude-fable-5")
        self.assertEqual(cfg_fable["mode"], "Claude_Fable")

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
        self.assertEqual(cfg_fuzzy_claude["mode"], "Claude_Fable")

        # Unknown model should gracefully fallback to default auto mode
        cfg_unknown = get_agent_config("some-nonexistent-model")
        self.assertIsNone(cfg_unknown["gptId"])
        self.assertEqual(cfg_unknown["mode"], "Magic")

    def test_server_list_models_endpoint(self):
        resp = list_models()
        self.assertEqual(resp["object"], "list")
        model_ids = [m["id"] for m in resp["data"]]
        self.assertIn("auto", model_ids)
        self.assertIn("fast", model_ids)
        self.assertIn("thinking", model_ids)
        self.assertIn("claude-sonnet-4.6", model_ids)
        self.assertIn("word", model_ids)
        self.assertIn("excel", model_ids)
        self.assertEqual(len(model_ids), len(AGENT_REGISTRY))


if __name__ == "__main__":
    unittest.main()
