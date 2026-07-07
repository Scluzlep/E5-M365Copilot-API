"""Pydantic request models for the OpenAI-compatible endpoints."""

from typing import Any, List, Optional, Union

from pydantic import BaseModel

from .config import MODEL_NAME


class ChatMessage(BaseModel):
    role: str
    # content is a plain string, or OpenAI "content parts" (list of dicts), or
    # null for some tool/assistant messages.
    content: Optional[Union[str, List[Any]]] = None
    reasoning_content: Optional[str] = None


class ChatCompletionRequest(BaseModel):
    messages: List[ChatMessage]
    model: Optional[str] = MODEL_NAME
    stream: bool = False
    # Copilot's own conversation id (returned in earlier responses). Pass it back
    # to continue that thread; omit it to start a fresh conversation. Outside
    # OpenAI's schema, but standard clients can set it via extra_body.
    conversation_id: Optional[str] = None
    plugins: Optional[List[Any]] = None
    tools: Optional[List[Any]] = None
    # Any other OpenAI fields (temperature, max_tokens, ...) are accepted and
    # ignored — Copilot's protocol doesn't expose those knobs.


class ClaudeMessage(BaseModel):
    role: str
    content: Union[str, List[Any]]


class ClaudeMessageRequest(BaseModel):
    model: str
    messages: List[ClaudeMessage]
    system: Optional[Union[str, List[Any]]] = None
    stream: bool = False
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
