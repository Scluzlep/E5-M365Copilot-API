"""Microsoft Copilot (E5 / Enterprise / Business Chat) Agent & Model Registry.

This module provides a unified routing table mapping standard OpenAI model names
(e.g., gpt-4o, gpt-5, o1, claude) and M365 capability titles (Word, Excel, Pages)
to Microsoft E5 Copilot's internal WebSocket routing parameters (`gptId`, `mode`,
and `optionsSets`).
"""

from typing import Dict, Any, Optional

# Complete E5 OptionsSets discovered from live telemetry captures (7MB session log).
# These activate full Business Chat capabilities: code interpreter, Flux v3 image gen,
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

AGENT_REGISTRY: Dict[str, Dict[str, Any]] = {
    # -------------------------------------------------------------------------
    # Standard & Fast Models (E5 自动 / 快速答复 / GPT 5.5 / 5.2 快速响应)
    # -------------------------------------------------------------------------
    "auto": {
        "mode": "Magic",
        "gptId": None,
        "optionsSets": DEFAULT_E5_OPTIONS_SETS.copy(),
        "description": "E5 自动模式 (自动决定要思考多长时间 / Auto)",
    },
    "自动": {
        "mode": "Magic",
        "gptId": None,
        "optionsSets": DEFAULT_E5_OPTIONS_SETS.copy(),
        "description": "E5 自动模式 (自动决定要思考多长时间 / Auto)",
    },
    "fast": {
        "mode": "Chat",
        "gptId": None,
        "optionsSets": DEFAULT_E5_OPTIONS_SETS.copy(),
        "description": "E5 快速答复 (立即回答 / Fast Response)",
    },
    "快速答复": {
        "mode": "Chat",
        "gptId": None,
        "optionsSets": DEFAULT_E5_OPTIONS_SETS.copy(),
        "description": "E5 快速答复 (立即回答 / Fast Response)",
    },
    "gpt-5.5-快速响应": {
        "mode": "Gpt_5_5_Chat",
        "gptId": None,
        "optionsSets": DEFAULT_E5_OPTIONS_SETS.copy(),
        "description": "E5 GPT 5.5 快速响应 (Fast Response)",
    },
    "gpt-5.5-fast": {
        "mode": "Gpt_5_5_Chat",
        "gptId": None,
        "optionsSets": DEFAULT_E5_OPTIONS_SETS.copy(),
        "description": "E5 GPT 5.5 快速响应 (Fast Response)",
    },
    "copilot": {
        "mode": "Magic",
        "gptId": None,
        "optionsSets": DEFAULT_E5_OPTIONS_SETS.copy(),
        "description": "Default E5 Business Chat",
    },
    "default": {
        "mode": "Magic",
        "gptId": None,
        "optionsSets": DEFAULT_E5_OPTIONS_SETS.copy(),
        "description": "Default E5 Business Chat",
    },

    # -------------------------------------------------------------------------
    # Deep Reasoning Models (E5 深度思考 / GPT 5.5 深度思考 / GPT-5)
    # -------------------------------------------------------------------------
    "thinking": {
        "mode": "Reasoning",
        "gptId": None,
        "optionsSets": DEFAULT_E5_OPTIONS_SETS + ["Agt_bizchat_enableGpt5ForHelix"],
        "description": "E5 深度思考 (思考更长时间以获得更好回答 / Deep Think)",
    },
    "深度思考": {
        "mode": "Reasoning",
        "gptId": None,
        "optionsSets": DEFAULT_E5_OPTIONS_SETS + ["Agt_bizchat_enableGpt5ForHelix"],
        "description": "E5 深度思考 (思考更长时间以获得更好回答 / Deep Think)",
    },
    "gpt-5.5-深度思考": {
        "mode": "Gpt_5_5_Reasoning",
        "gptId": None,
        "optionsSets": DEFAULT_E5_OPTIONS_SETS + ["Agt_bizchat_enableGpt5ForHelix"],
        "description": "E5 GPT 5.5 深度思考 (Thinking Mode)",
    },
    "gpt-5.5-thinking": {
        "mode": "Gpt_5_5_Reasoning",
        "gptId": None,
        "optionsSets": DEFAULT_E5_OPTIONS_SETS + ["Agt_bizchat_enableGpt5ForHelix"],
        "description": "E5 GPT 5.5 深度思考 (Thinking Mode)",
    },
    "reasoning": {
        "mode": "Reasoning",
        "gptId": None,
        "optionsSets": DEFAULT_E5_OPTIONS_SETS + ["Agt_bizchat_enableGpt5ForHelix"],
        "description": "E5 Deep Reasoning Mode",
    },
    "gpt-5.5": {
        "mode": "Gpt_5_5_Reasoning",
        "gptId": None,
        "optionsSets": DEFAULT_E5_OPTIONS_SETS + ["Agt_bizchat_enableGpt5ForHelix"],
        "description": "OpenAI GPT-5.5 Flagship (Thinking Mode)",
    },

    # -------------------------------------------------------------------------
    # Anthropic / Claude (留着先 / Keep for now in E5)
    # -------------------------------------------------------------------------
    "anthropic": {
        "mode": "Magic",
        "gptId": "P_bbef1ffa-25db-4bb9-870d-afb22f41a4e9",
        "optionsSets": DEFAULT_E5_OPTIONS_SETS.copy(),
        "description": "Anthropic Claude integrated in E5 Business Chat",
    },
    "claude": {
        "mode": "Magic",
        "gptId": "P_bbef1ffa-25db-4bb9-870d-afb22f41a4e9",
        "optionsSets": DEFAULT_E5_OPTIONS_SETS.copy(),
        "description": "Anthropic Claude integrated in E5 Business Chat",
    },
    "claude-opus-4.8": {
        "mode": "Magic",
        "gptId": "P_bbef1ffa-25db-4bb9-870d-afb22f41a4e9",
        "optionsSets": DEFAULT_E5_OPTIONS_SETS.copy(),
        "description": "Anthropic Claude Opus 4.8 (Most Honest / Subagents)",
    },
    "claude-fable-5": {
        "mode": "Magic",
        "gptId": "P_bbef1ffa-25db-4bb9-870d-afb22f41a4e9",
        "optionsSets": DEFAULT_E5_OPTIONS_SETS.copy(),
        "description": "Anthropic Claude Fable 5 General Branch in E5",
    },
    "claude-mythos-5": {
        "mode": "Magic",
        "gptId": "P_bbef1ffa-25db-4bb9-870d-afb22f41a4e9",
        "optionsSets": DEFAULT_E5_OPTIONS_SETS.copy(),
        "description": "Anthropic Claude Mythos 5 in E5",
    },

    # -------------------------------------------------------------------------
    # M365 Capability Agents (Word, Excel, PowerPoint, Designer, Pages)
    # -------------------------------------------------------------------------
    "word": {
        "mode": "Magic",
        "gptId": "word",
        "optionsSets": DEFAULT_E5_OPTIONS_SETS.copy(),
        "description": "M365 Word Agent",
    },
    "excel": {
        "mode": "Magic",
        "gptId": "excel",
        "optionsSets": DEFAULT_E5_OPTIONS_SETS.copy(),
        "description": "M365 Excel Agent",
    },
    "powerpoint": {
        "mode": "Magic",
        "gptId": "powerpoint",
        "optionsSets": DEFAULT_E5_OPTIONS_SETS.copy(),
        "description": "M365 PowerPoint Agent",
    },
    "designer": {
        "mode": "Magic",
        "gptId": "designer",
        "optionsSets": DEFAULT_E5_OPTIONS_SETS.copy(),
        "description": "M365 Designer / Image Gen Agent",
    },
    "pages": {
        "mode": "Magic",
        "gptId": "pages",
        "optionsSets": DEFAULT_E5_OPTIONS_SETS.copy(),
        "description": "M365 Pages Agent",
    },
}


def get_agent_config(model_name: Optional[str] = None) -> Dict[str, Any]:
    """Resolve an OpenAI model string or agent title to E5 WebSocket routing parameters.

    Returns a dictionary containing:
        - `mode`: The E5 tone/mode (e.g. "Magic", "Chat", "Reasoning", "Gpt_5_5_Reasoning").
        - `gptId`: The plugin or custom agent identifier (e.g. "P_xxx", None for default).
        - `optionsSets`: List of feature flag strings to send in setOptions / send frames.
        - `description`: Human-readable summary of the resolved agent.
    """
    if not model_name:
        return AGENT_REGISTRY["copilot"].copy()

    clean_name = model_name.strip().lower()

    if clean_name in AGENT_REGISTRY:
        return AGENT_REGISTRY[clean_name].copy()

    # Intelligent prefix matching for E5 UI Modes & AI families
    if any(clean_name.startswith(p) for p in ("claude", "fable", "mythos", "opus", "anthropic")):
        return AGENT_REGISTRY["claude"].copy()
    if any(p in clean_name for p in ("5.5-thinking", "5.5-reasoning", "5.5 深度思考", "5.5-深度思考", "5_5_reasoning")):
        return AGENT_REGISTRY["gpt-5.5-thinking"].copy()
    if any(p in clean_name for p in ("5.5-fast", "5.5 快速", "5_5_chat", "5.5-快速响应", "5.5 快速响应")):
        return AGENT_REGISTRY["gpt-5.5-fast"].copy()
    if any(p in clean_name for p in ("5.5", "5_5", "reasoning", "thinking", "深度思考", "deep")):
        return AGENT_REGISTRY["thinking"].copy()
    if any(clean_name.startswith(p) or p in clean_name for p in ("fast", "快速", "instant", "chat")):
        return AGENT_REGISTRY["fast"].copy()
    if any(clean_name.startswith(p) or p in clean_name for p in ("auto", "自动", "magic")):
        return AGENT_REGISTRY["auto"].copy()
    if any(clean_name.startswith(p) for p in ("default", "copilot")):
        return AGENT_REGISTRY["copilot"].copy()

    # If the caller passed an explicit E5 plugin / custom GPT identifier (e.g. "P_1234...", "Agt_...")
    if model_name.startswith("P_") or model_name.startswith("Agt_") or (len(model_name) == 36 and "-" in model_name):
        return {
            "mode": "Magic",
            "gptId": model_name,
            "optionsSets": DEFAULT_E5_OPTIONS_SETS.copy(),
            "description": f"Custom E5 Agent ID: {model_name}",
        }

    # Fallback gracefully to default E5 Copilot for unrecognized model names
    print(f"[AgentRegistry] Model '{model_name}' not found in registry; defaulting to E5 Copilot.")
    return AGENT_REGISTRY["copilot"].copy()
