"""Simulated Tool Calling engine for E5-M365Copilot-API.

Ported and adapted from M365Bridge (Go implementation) to provide provider-neutral
tool calling simulation, JSON candidate extraction, scoring, allowlist validation,
and SSE event encoding for OpenAI, Anthropic, and Responses APIs.
"""

import json
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple, Union

from copilot.tool_hygiene import deduplicate_tool_calls

def flatten_tools(input_tools: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Flatten and normalize tool definitions into a lookup table by tool_name."""
    result = {}
    if not input_tools:
        return result
        
    for t in input_tools:
        if not isinstance(t, dict):
            continue
            
        t_type = t.get("type", "function")
        if t_type == "function" or "function" in t:
            fn = t.get("function", {})
            name = fn.get("name") if isinstance(fn, dict) else t.get("name")
            desc = fn.get("description") if isinstance(fn, dict) else t.get("description", "")
            params = fn.get("parameters") if isinstance(fn, dict) else t.get("parameters", {})
            if name:
                result[name] = {
                    "name": name,
                    "description": desc,
                    "parameters": params or {},
                    "required": params.get("required", []) if isinstance(params, dict) else []
                }
        elif "name" in t:  # Anthropic flat tool definition or standard tool
            name = t.get("name")
            desc = t.get("description", "")
            schema = t.get("input_schema") or t.get("parameters") or {}
            required = schema.get("required", []) if isinstance(schema, dict) else []
            if name:
                result[name] = {
                    "name": name,
                    "description": desc,
                    "parameters": schema,
                    "required": required
                }
    return result

def get_required_args_map(tools_map: Dict[str, Dict[str, Any]]) -> Dict[str, List[str]]:
    """Map tool_name to list of required argument names."""
    res = {}
    for name, tool in tools_map.items():
        res[name] = tool.get("required", [])
    return res

def build_simulation_prompt(request_json: str, tools_map: Dict[str, Dict[str, Any]], provider: str = "openai") -> str:
    """Build system prompt instructing Copilot to return a provider-native tool call JSON object."""
    required_map = get_required_args_map(tools_map)
    
    if provider == "anthropic":
        expected_format = '{"type":"message","role":"assistant","content":[{"type":"tool_use","id":"toolu_1","name":"tool_name","input":{}}],"stop_reason":"tool_use"}'
    elif provider == "responses":
        expected_format = '{"id":"resp_1","object":"response","output_item":{"type":"function_call","name":"tool_name","call_id":"call_1","arguments":"{}"},"status":"completed"}'
    else:
        expected_format = '{"choices":[{"message":{"role":"assistant","content":null,"tool_calls":[{"id":"call_1","type":"function","function":{"name":"tool_name","arguments":"{}"}}]},"finish_reason":"tool_calls"}]}'

    prompt = (
        "You are an adapter that must produce exactly one valid JSON object.\n"
        "Do not return any conversational text, prose, or explanation outside the JSON object.\n"
        "Use ONLY tool names declared in the request below. Never invent tool names.\n"
        "Every required argument specified in the tool definition must be present, non-null, and non-empty.\n"
        "Expected JSON shape:\n"
        f"{expected_format}\n\n"
        "BEGIN_REQUEST_JSON\n"
        f"{request_json}\n"
        "END_REQUEST_JSON\n\n"
        f"Required argument constraints: {json.dumps(required_map)}\n"
    )
    return prompt

def balanced_end(text: str, start: int) -> Optional[int]:
    """Find matching closing bracket for '{' or '[' starting at index `start`."""
    stack = []
    quoted = False
    escaped = False
    
    for i in range(start, len(text)):
        char = text[i]
        if quoted:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quoted = False
            continue
            
        if char == '"':
            quoted = True
            continue
            
        if char in ('{', '['):
            stack.append(char)
        elif char in ('}', ']'):
            if not stack:
                return None
            opening = stack[-1]
            if (opening == '{' and char != '}') or (opening == '[' and char != ']'):
                return None
            stack.pop()
            if not stack:
                return i
    return None

def extract_json_candidates(text: str) -> List[str]:
    """Extract candidate JSON strings from raw output text (markdown code blocks, raw JSON, etc.)."""
    candidates = []
    seen = set()
    
    def add_candidate(cand: str):
        cand = cand.strip()
        if not cand or cand in seen:
            return
        try:
            json.loads(cand)
            seen.add(cand)
            candidates.append(cand)
        except Exception:
            pass

    # 1. Try full text
    add_candidate(text)
    
    # 2. Extract markdown ```json ... ``` blocks
    lines = text.splitlines()
    in_fence = False
    fenced_lines = []
    for line in lines:
        trimmed = line.strip()
        if trimmed.startswith("```"):
            if in_fence:
                add_candidate("\n".join(fenced_lines))
                fenced_lines = []
            in_fence = not in_fence
            continue
        if in_fence:
            fenced_lines.append(line)
            
    # 3. Scan for balanced '{ ... }' or '[ ... ]'
    for start in range(len(text)):
        if text[start] in ('{', '['):
            end = balanced_end(text, start)
            if end is not None:
                add_candidate(text[start : end + 1])
                
    return candidates

def score_payload(payload: Dict[str, Any], provider: str = "openai") -> int:
    """Score candidate JSON payload based on how well it fits provider tool call response shapes."""
    if not isinstance(payload, dict):
        return 0
        
    score = 0
    # Deduct if it looks like a request instead of a response
    if ("messages" in payload or "input" in payload) and ("tools" in payload or "tool_choice" in payload):
        score -= 12
        
    if provider == "anthropic":
        content = payload.get("content")
        if isinstance(content, list):
            score += 8
            for item in content:
                if isinstance(item, dict) and item.get("type") == "tool_use":
                    score += 10
        if payload.get("type") == "message":
            score += 4
        if payload.get("stop_reason") == "tool_use":
            score += 5
        return score
        
    if provider == "responses":
        if payload.get("object") == "response":
            score += 8
        if "output_item" in payload:
            out = payload.get("output_item", {})
            if isinstance(out, dict) and out.get("type") in ("function_call", "tool_call"):
                score += 10
        if "output" in payload and isinstance(payload.get("output"), list):
            score += 6
        return score

    # OpenAI Chat Completions default
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        score += 8
        first = choices[0]
        if isinstance(first, dict):
            msg = first.get("message", {})
            if isinstance(msg, dict) and "tool_calls" in msg and msg.get("tool_calls"):
                score += 10
            if first.get("finish_reason") == "tool_calls":
                score += 4
    if payload.get("object") == "chat.completion":
        score += 3
    return score

def parse_simulation_result(text: str, declared_tools: List[Dict[str, Any]], provider: str = "openai") -> Dict[str, Any]:
    """Parse raw text, extract best candidate JSON, validate tools, and return SimulationResult dictionary."""
    tools_map = flatten_tools(declared_tools)
    candidates = extract_json_candidates(text)
    
    best_score = 0
    best_payload = None
    best_cand = None
    for cand in candidates:
        try:
            payload = json.loads(cand)
            score = score_payload(payload, provider)
            if score > best_score:
                best_score = score
                best_payload = payload
                best_cand = cand
        except Exception:
            continue
            
    if not best_payload:
        return {
            "content": text,
            "tool_calls": [],
            "finish_reason": "stop" if provider != "anthropic" else "end_turn",
            "has_payload": False
        }
        
    extracted_calls = []
    extracted_content = ""
    
    if provider == "anthropic":
        content_blocks = best_payload.get("content", [])
        if isinstance(content_blocks, list):
            for i, block in enumerate(content_blocks):
                if isinstance(block, dict):
                    if block.get("type") == "tool_use":
                        t_name = block.get("name", "")
                        t_id = block.get("id") or f"toolu_{uuid.uuid4().hex[:8]}"
                        t_input = block.get("input") or {}
                        extracted_calls.append({
                            "id": t_id,
                            "name": t_name,
                            "arguments": t_input if isinstance(t_input, dict) else {}
                        })
                    elif block.get("type") == "text":
                        extracted_content += block.get("text", "")
    elif provider == "responses":
        out_item = best_payload.get("output_item")
        if isinstance(out_item, dict) and out_item.get("type") in ("function_call", "tool_call"):
            t_name = out_item.get("name", "")
            t_id = out_item.get("call_id") or out_item.get("id") or f"call_{uuid.uuid4().hex[:8]}"
            t_args = out_item.get("arguments") or "{}"
            if isinstance(t_args, str):
                try:
                    t_args_dict = json.loads(t_args)
                except Exception:
                    t_args_dict = {}
            else:
                t_args_dict = t_args
            extracted_calls.append({
                "id": t_id,
                "name": t_name,
                "arguments": t_args_dict
            })
    else:  # OpenAI
        choices = best_payload.get("choices", [])
        if choices and isinstance(choices[0], dict):
            msg = choices[0].get("message", {})
            extracted_content = msg.get("content") or ""
            t_calls = msg.get("tool_calls", [])
            if isinstance(t_calls, list):
                for i, tc in enumerate(t_calls):
                    if isinstance(tc, dict):
                        fn = tc.get("function", {})
                        t_name = fn.get("name") if isinstance(fn, dict) else tc.get("name", "")
                        t_args_raw = fn.get("arguments") if isinstance(fn, dict) else tc.get("arguments", "{}")
                        if isinstance(t_args_raw, str):
                            try:
                                t_args_dict = json.loads(t_args_raw)
                            except Exception:
                                t_args_dict = {}
                        else:
                            t_args_dict = t_args_raw or {}
                        t_id = tc.get("id") or f"call_{uuid.uuid4().hex[:8]}"
                        extracted_calls.append({
                            "id": t_id,
                            "name": t_name,
                            "arguments": t_args_dict
                        })

    # Validate extracted calls against declared tools & required parameters
    valid_calls = []
    
    # Prepend any text generated before the JSON block to the content
    if best_cand and text:
        pre_text = text.split(best_cand)[0].strip()
        if pre_text:
            extracted_content = pre_text + ("\n" + extracted_content if extracted_content else "")
            
    required_args_map = get_required_args_map(tools_map)
    for call in extracted_calls:
        name = call["name"]
        if name not in tools_map:
            continue
        req_fields = required_args_map.get(name, [])
        args = call["arguments"]
        if not isinstance(args, dict):
            args = {}
            call["arguments"] = args
        missing = [f for f in req_fields if f not in args or args[f] is None or (isinstance(args[f], str) and not args[f].strip())]
        if missing:
            print(f"[ToolSim] Dropped tool call '{name}' due to missing required arguments: {missing}")
            continue
        valid_calls.append(call)
        
    valid_calls, drop_reasons = deduplicate_tool_calls(valid_calls, by_name_and_args=True)
    if drop_reasons:
        for reason in drop_reasons:
            print(f"[ToolSim] {reason}")

    has_calls = len(valid_calls) > 0
    finish_reason = "tool_calls" if has_calls else ("stop" if provider != "anthropic" else "end_turn")
    if provider == "anthropic" and has_calls:
        finish_reason = "tool_use"
        
    return {
        "content": extracted_content if not has_calls else (extracted_content or None),
        "tool_calls": valid_calls,
        "finish_reason": finish_reason,
        "has_payload": True
    }
