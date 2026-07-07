import json
import uuid

def new_msg_id() -> str:
    """A fresh ``msg_...`` id, as the Anthropic API returns."""
    return f"msg_{uuid.uuid4().hex}"

def sse_event(event_type: str, payload: dict) -> str:
    """Serialize a payload as a Server-Sent Events line."""
    return f"event: {event_type}\ndata: {json.dumps(payload)}\n\n"

def message_response(text: str, model: str) -> dict:
    """A non-streaming Claude message object."""
    return {
        "id": new_msg_id(),
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [
            {
                "type": "text",
                "text": text
            }
        ],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 0, "output_tokens": 0}
    }

def stream_message_start(msg_id: str, model: str) -> str:
    payload = {
        "type": "message_start",
        "message": {
            "id": msg_id,
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0}
        }
    }
    return sse_event("message_start", payload)

def stream_content_block_start(index: int = 0) -> str:
    payload = {
        "type": "content_block_start",
        "index": index,
        "content_block": {
            "type": "text",
            "text": ""
        }
    }
    return sse_event("content_block_start", payload)

def stream_content_block_delta(index: int, text: str) -> str:
    payload = {
        "type": "content_block_delta",
        "index": index,
        "delta": {
            "type": "text_delta",
            "text": text
        }
    }
    return sse_event("content_block_delta", payload)

def stream_content_block_stop(index: int = 0) -> str:
    payload = {
        "type": "content_block_stop",
        "index": index
    }
    return sse_event("content_block_stop", payload)

def stream_message_delta(stop_reason: str = "end_turn") -> str:
    payload = {
        "type": "message_delta",
        "delta": {
            "stop_reason": stop_reason,
            "stop_sequence": None
        },
        "usage": {"output_tokens": 0}
    }
    return sse_event("message_delta", payload)

def stream_message_stop() -> str:
    payload = {
        "type": "message_stop"
    }
    return sse_event("message_stop", payload)
