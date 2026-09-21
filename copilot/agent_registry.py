"""Microsoft Copilot (E5 / Enterprise / Business Chat) Agent & Model Registry.

This module provides a unified routing table mapping standard OpenAI/Claude model names
to Microsoft E5 Copilot's internal Substrate WebSocket routing parameters (`gptId`, `mode`/`tone`,
and `optionsSets`).
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

# Complete E5 OptionsSets discovered from live telemetry captures.
# Activates full Business Chat capabilities: code interpreter, Flux v3 image gen,
# rich web answers, page citations, and deep reasoning flights.
DEFAULT_E5_OPTIONS_SETS = [
    "search_result_progress_messages_with_search_queries",
    "update_textdoc_response_after_streaming",
    "deepleo_networking_timeout_10minutes_canmore",
    "cwc_flux_image",
    "cwc_code_interpreter",
    "cwc_code_interpreter_amsfix",
    "cwcfluxgptv",
    "flux_v3_gptv_enable_upload_multi_image_in_turn_wo_ch",
    "gptvnorm2048",
    "cwc_code_interpreter_citation_fix",
    "code_interpreter_interactive_charts",
    "cwc_code_interpreter_interactive_charts_inline_image",
    "code_interpreter_matplotlib_patching",
    "cwc_fileupload_odb",
    "update_memory_plugin",
    "add_custom_instructions",
    "cwc_flux_v3",
    "flux_v3_progress_messages",
    "enable_batch_token_processing",
    "enable_gg_gpt",
    "flux_v3_references",
    "flux_v3_references_entities",
    "flux_v3_image_gen_enable_dimensions",
    "flux_v3_image_gen_enable_non_watermarked_storage",
    "flux_v3_image_gen_enable_icon_dimensions",
    "flux_v3_image_gen_enable_system_text_with_params",
    "flux_v3_image_gen_enable_designer_dimensions_meta_prompting_in_system_prompts",
    "flux_v3_image_gen_enable_story",
    "rich_responses",
    "pages_citations",
    "pages_citations_multiturn",
]

REASONING_OPTIONS_SETS = DEFAULT_E5_OPTIONS_SETS + ["Agt_bizchat_enableGpt5ForHelix"]

# 20 Official M365 Substrate Tones verified by active tenant probing
BASE_TONE_DEFINITIONS = [
    {"tone": "Magic", "display": "Copilot_自动", "desc": "Copilot 自动选模 (Auto)"},
    {"tone": "Chat", "display": "Copilot_快速答复", "desc": "Copilot 快速答复 (Fast)"},
    {"tone": "Reasoning", "display": "Copilot_深度思考", "desc": "Copilot 深度思考 (Deep Reasoning)"},
    {"tone": "Claude_Sonnet", "display": "claude-sonnet-4-6", "desc": "Claude Sonnet (Tool-calling verified)"},
    {"tone": "Claude_Sonnet_Reasoning", "display": "claude-sonnet-4-5", "desc": "Claude Sonnet 思考 (Tool-calling verified)"},
    {"tone": "Claude_Fable", "display": "claude-fable-5", "desc": "Claude Fable 5"},
    {"tone": "Claude_Opus", "display": "claude-opus", "desc": "Claude Opus (Studio/Tools verified)"},
    {"tone": "Gpt_6_Astra", "display": "gpt-6_Chat", "desc": "GPT-6 Astra 快速 (Chat & Studio verified)"},
    {"tone": "Gpt_6_Reasoning", "display": "gpt-6", "desc": "GPT-6 思考 (Studio tool workflow required)"},
    {"tone": "Gpt_5_6_Chat", "display": "gpt-5.6_Chat", "desc": "GPT 5.6 快速响应"},
    {"tone": "Gpt_5_6_Reasoning", "display": "gpt-5.6", "desc": "GPT 5.6 深度思考"},
    {"tone": "Gpt_5_5_Chat", "display": "gpt-5.5_Chat", "desc": "GPT 5.5 快速响应"},
    {"tone": "Gpt_5_5_Reasoning", "display": "gpt-5.5", "desc": "GPT 5.5 深度思考"},
    {"tone": "Gpt_5_4_Chat", "display": "gpt-5.4_Chat", "desc": "GPT 5.4 快速响应"},
    {"tone": "Gpt_5_4_Reasoning", "display": "gpt-5.4", "desc": "GPT 5.4 深度思考"},
    {"tone": "Gpt_5_3_Chat", "display": "gpt-5.3_Chat", "desc": "GPT 5.3 快速响应"},
    {"tone": "Gpt_5_3_Reasoning", "display": "gpt-5.3", "desc": "GPT 5.3 深度思考"},
    {"tone": "Gpt_5_2_Chat", "display": "gpt-5.2_Chat", "desc": "GPT 5.2 快速响应"},
    {"tone": "Gpt_5_2_Reasoning", "display": "gpt-5.2", "desc": "GPT 5.2 深度思考"},
    {"tone": "Grok_4_5", "display": "grok-4.5", "desc": "Grok 4.5 结构化文本"},
]

AGENT_REGISTRY: Dict[str, Dict[str, Any]] = {}

# Populate base models and their '-持续' (persist session) variants
for item in BASE_TONE_DEFINITIONS:
    tone = item["tone"]
    display = item["display"]
    desc = item["desc"]
    opts = REASONING_OPTIONS_SETS.copy() if "reasoning" in tone.lower() else DEFAULT_E5_OPTIONS_SETS.copy()

    # Normal variant
    AGENT_REGISTRY[display.lower()] = {
        "mode": tone,
        "gptId": None,
        "optionsSets": opts.copy(),
        "description": desc,
        "persist_session": False,
    }
    # Persist variant
    AGENT_REGISTRY[f"{display.lower()}-持续"] = {
        "mode": tone,
        "gptId": None,
        "optionsSets": opts.copy(),
        "description": f"{desc} (固定复用会话)",
        "persist_session": True,
    }
    # Direct tone value variant
    AGENT_REGISTRY[tone.lower()] = {
        "mode": tone,
        "gptId": None,
        "optionsSets": opts.copy(),
        "description": desc,
        "persist_session": False,
    }
    AGENT_REGISTRY[f"{tone.lower()}-持续"] = {
        "mode": tone,
        "gptId": None,
        "optionsSets": opts.copy(),
        "description": f"{desc} (固定复用会话)",
        "persist_session": True,
    }

# Convenient aliases
AGENT_REGISTRY["auto"] = AGENT_REGISTRY["copilot_自动"]
AGENT_REGISTRY["fast"] = AGENT_REGISTRY["copilot_快速答复"]
AGENT_REGISTRY["thinking"] = AGENT_REGISTRY["copilot_深度思考"]
AGENT_REGISTRY["copilot"] = AGENT_REGISTRY["copilot_自动"]
AGENT_REGISTRY["claude-sonnet-4.6"] = AGENT_REGISTRY["claude-sonnet-4-6"]
AGENT_REGISTRY["claude-opus-4.8"] = AGENT_REGISTRY["claude-opus"]
AGENT_REGISTRY["claude-mythos-5"] = {
    "mode": "Claude_Mythos",
    "gptId": None,
    "optionsSets": DEFAULT_E5_OPTIONS_SETS.copy(),
    "description": "Claude Mythos 5 in E5",
    "persist_session": False,
}

# Office capability agents
for agent_id, desc in [
    ("word", "M365 Word Agent"),
    ("excel", "M365 Excel Agent"),
    ("powerpoint", "M365 PowerPoint Agent"),
    ("designer", "M365 Designer / Image Gen Agent"),
    ("pages", "M365 Pages Agent"),
]:
    AGENT_REGISTRY[agent_id] = {
        "mode": "Magic",
        "gptId": agent_id,
        "optionsSets": DEFAULT_E5_OPTIONS_SETS.copy(),
        "description": desc,
        "persist_session": False,
    }


def get_agent_config(model_name: Optional[str] = None) -> Dict[str, Any]:
    """Resolve a model name string to E5 WebSocket routing parameters."""
    if not model_name:
        res = AGENT_REGISTRY["copilot_自动"].copy()
        res["persist_session"] = False
        return res

    clean_name = model_name.strip().lower()
    persist = False

    # Check for persistence suffix
    if clean_name.endswith("-持续") or clean_name.endswith(":persist"):
        persist = True
        clean_name = clean_name.replace("-持续", "").replace(":persist", "")

    config: Optional[Dict[str, Any]] = None

    if clean_name in AGENT_REGISTRY:
        config = AGENT_REGISTRY[clean_name].copy()
    elif f"{clean_name}-持续" in AGENT_REGISTRY:
        config = AGENT_REGISTRY[f"{clean_name}-持续"].copy()
    else:
        # Fuzzy matching
        if "mythos" in clean_name:
            config = AGENT_REGISTRY["claude-mythos-5"].copy()
        elif "grok" in clean_name:
            config = AGENT_REGISTRY["grok-4.5"].copy()
        elif "gpt-6" in clean_name or "gpt6" in clean_name:
            if "chat" in clean_name or "astra" in clean_name:
                config = AGENT_REGISTRY["gpt-6_chat"].copy()
            else:
                config = AGENT_REGISTRY["gpt-6"].copy()
        elif "5.6" in clean_name:
            if any(k in clean_name for k in ("chat", "fast", "快速", "快速响应")):
                config = AGENT_REGISTRY["gpt-5.6_chat"].copy()
            else:
                config = AGENT_REGISTRY["gpt-5.6"].copy()
        elif "5.5" in clean_name:
            if any(k in clean_name for k in ("chat", "fast", "快速", "快速响应")):
                config = AGENT_REGISTRY["gpt-5.5_chat"].copy()
            else:
                config = AGENT_REGISTRY["gpt-5.5"].copy()
        elif "5.4" in clean_name:
            if any(k in clean_name for k in ("chat", "fast", "快速", "快速响应")):
                config = AGENT_REGISTRY["gpt-5.4_chat"].copy()
            else:
                config = AGENT_REGISTRY["gpt-5.4"].copy()
        elif "5.3" in clean_name:
            if any(k in clean_name for k in ("chat", "fast", "快速", "快速响应")):
                config = AGENT_REGISTRY["gpt-5.3_chat"].copy()
            else:
                config = AGENT_REGISTRY["gpt-5.3"].copy()
        elif "5.2" in clean_name:
            if any(k in clean_name for k in ("chat", "fast", "快速", "快速响应")):
                config = AGENT_REGISTRY["gpt-5.2_chat"].copy()
            else:
                config = AGENT_REGISTRY["gpt-5.2"].copy()
        elif "opus" in clean_name:
            config = AGENT_REGISTRY["claude-opus"].copy()
        elif "fable" in clean_name:
            config = AGENT_REGISTRY["claude-fable-5"].copy()
        elif "sonnet" in clean_name or "claude" in clean_name:
            if any(k in clean_name for k in ("thinking", "reasoning", "4.5", "4-5")):
                config = AGENT_REGISTRY["claude-sonnet-4-5"].copy()
            else:
                config = AGENT_REGISTRY["claude-sonnet-4-6"].copy()
        elif any(k in clean_name for k in ("thinking", "deep", "reasoning", "深度思考")):
            config = AGENT_REGISTRY["copilot_深度思考"].copy()
        elif any(k in clean_name for k in ("fast", "chat", "快速答复", "快速响应")):
            config = AGENT_REGISTRY["copilot_快速答复"].copy()
        elif model_name.startswith("P_") or model_name.startswith("Agt_") or (len(model_name) == 36 and "-" in model_name):
            config = {
                "mode": "Magic",
                "gptId": model_name,
                "optionsSets": DEFAULT_E5_OPTIONS_SETS.copy(),
                "description": f"Custom E5 Agent ID: {model_name}",
                "persist_session": persist,
            }
        else:
            config = AGENT_REGISTRY["copilot_自动"].copy()

    if persist:
        config["persist_session"] = True
    return config


def get_all_models_data() -> List[Dict[str, Any]]:
    """Return all standard models in OpenAI /v1/models response format."""
    now = int(time.time())
    models = []
    # 20 Base Tones x 2 Variants = 40 models
    for item in BASE_TONE_DEFINITIONS:
        display = item["display"]
        models.append({
            "id": display,
            "object": "model",
            "created": now,
            "owned_by": "microsoft-copilot",
            "permission": [],
            "root": display,
            "parent": None,
        })
        models.append({
            "id": f"{display}-持续",
            "object": "model",
            "created": now,
            "owned_by": "microsoft-copilot",
            "permission": [],
            "root": display,
            "parent": None,
        })
    # Add capability agents and aliases
    for extra_id in ["word", "excel", "powerpoint", "designer", "pages", "auto", "fast", "thinking", "claude-sonnet-4.6"]:
        models.append({
            "id": extra_id,
            "object": "model",
            "created": now,
            "owned_by": "microsoft-m365" if extra_id in ["word", "excel", "powerpoint", "designer", "pages"] else "microsoft-copilot",
            "permission": [],
            "root": extra_id,
            "parent": None,
        })
    return models
