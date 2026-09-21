"""Tool Hygiene: deduplication and sanitation of tool calls for OpenAI and Anthropic APIs.

LLM simulated tool-calling generates JSON/Markdown blocks in prose. In multi-turn
or repetitive generations, the model may output duplicate tool calls (same tool
name and same arguments, or duplicate tool_call ids). Anthropic rejects duplicate
tool_use IDs or mismatched rounds with 400 Bad Request.

This module provides idempotent deduplication and safety filters to prevent client 400 crashes.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple


def _canonicalize_args(args_val: Any) -> str:
    """Produce a deterministic, whitespace-normalized string representation of arguments."""
    if isinstance(args_val, dict):
        try:
            return json.dumps(args_val, sort_keys=True, ensure_ascii=False)
        except Exception:
            return str(args_val)
    if isinstance(args_val, str):
        # Try parsing as JSON to re-serialize deterministically
        try:
            parsed = json.loads(args_val)
            if isinstance(parsed, dict):
                return json.dumps(parsed, sort_keys=True, ensure_ascii=False)
        except Exception:
            pass
        return args_val.strip()
    return str(args_val)


def deduplicate_tool_calls(
    tool_calls: List[Dict[str, Any]],
    *,
    by_name_and_args: bool = True,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Deduplicate tool calls in a response.

    Returns:
        (kept_calls, dropped_reasons)
    """
    seen_ids = set()
    seen_signatures = set()
    kept: List[Dict[str, Any]] = []
    reasons: List[str] = []

    for call in tool_calls:
        if not isinstance(call, dict):
            continue

        call_id = str(call.get("id") or "")
        fn = call.get("function")
        if isinstance(fn, dict):
            name = str(fn.get("name") or call.get("name") or "")
            raw_args = fn.get("arguments") if "arguments" in fn else (call.get("arguments") or "")
        else:
            name = str(call.get("name") or "")
            raw_args = call.get("arguments") or ""
        norm_args = _canonicalize_args(raw_args)

        # 1. Deduplicate by explicit call_id if present
        if call_id and call_id in seen_ids:
            reasons.append(f"Dropped tool call with duplicate id '{call_id}'")
            continue

        # 2. Deduplicate by (name, arguments)
        if by_name_and_args and name:
            sig = (str(name), norm_args)
            if sig in seen_signatures:
                reasons.append(f"Dropped duplicate tool call for '{name}' with identical arguments")
                continue
            seen_signatures.add(sig)

        if call_id:
            seen_ids.add(call_id)
        kept.append(call)

    return kept, reasons
