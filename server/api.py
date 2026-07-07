"""FastAPI app wiring Copilot onto the OpenAI Chat Completions API."""

import time
import asyncio
import secrets
import os

from fastapi import FastAPI, Depends, BackgroundTasks, HTTPException, status, WebSocket, Request, Header
from fastapi.responses import JSONResponse, StreamingResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

from copilot.models import ImageResponse
from copilot.utils import mask_token

from .config import MODEL_NAME
from .openai_format import (
    completion_response,
    new_id,
    sse_event,
    stream_chunk,
)
from .schemas import ChatCompletionRequest, ClaudeMessageRequest
from server.prompt import messages_to_prompt, extract_files
from server.router import router
from .claude_format import (
    message_response,
    new_msg_id,
    stream_message_start,
    stream_content_block_start,
    stream_content_block_delta,
    stream_content_block_stop,
    stream_message_delta,
    stream_message_stop
)

app = FastAPI(title="Copilot OpenAI-compatible API", version="1.0.0")
security = HTTPBearer(auto_error=False)

from server.api_azure_config import router as azure_config_router
app.include_router(azure_config_router)

# (Locks and rate limits are now managed per-account in server/accounts.py)

from .accounts import pool, RateLimitExceeded

import re

_RE_BRACKET_CITE = re.compile(r'【\d+-[a-zA-Z0-9]+】')
_RE_UNICODE_CITE = re.compile(r'\ue200cite((?:\ue202[^\ue201\ue202]+)+)\ue201')
_RE_OLD_CITE = re.compile(r'\u200b?cite((?:turn\d+search\d+)+)\u200b?')
_RE_E200_STRIP = re.compile(r'\ue200.*')

class StreamCleaner:
    def __init__(self):
        self.buffer = ""
        self.citations = {}
        self.ref_map = {}
        self.ref_counter = 1

    def add_citations(self, citations: dict):
        self.citations.update(citations)

    def _replace_citations(self, match) -> str:
        cite_block = match.group(0)
        if '\ue202' in cite_block:
            refs = re.findall(r'\ue202([^\ue201\ue202]+)', cite_block)
        else:
            refs = re.findall(r'turn\d+search\d+', cite_block)
            
        if not refs:
            return ""
        
        replacements = []
        for ref in refs:
            if ref not in self.ref_map:
                self.ref_map[ref] = self.ref_counter
                self.ref_counter += 1
            idx = self.ref_map[ref]
            
            # Inline we just put [1]
            replacements.append(f"[{idx}]")
                
        return "".join(replacements)

    def process(self, chunk: str) -> str:
        self.buffer += chunk
        self.buffer = _RE_BRACKET_CITE.sub('', self.buffer)
        
        # New Copilot cite format: citeturn1search20 (\ue200cite\ue202turn...\ue201)
        self.buffer = _RE_UNICODE_CITE.sub(self._replace_citations, self.buffer)
        # Old cite format fallback / Strip unicode-stripped remnants
        self.buffer = _RE_OLD_CITE.sub(self._replace_citations, self.buffer)
        self.buffer = self.buffer.replace('\u200b', '')
        
        idx_bracket = self.buffer.rfind('【')
        idx_cite = self.buffer.rfind('citeturn')
        idx_e200 = self.buffer.rfind('\ue200')
        
        hold_idx = -1
        if idx_bracket != -1 and '】' not in self.buffer[idx_bracket:]:
            hold_idx = idx_bracket
            
        if idx_cite != -1 and (len(self.buffer) - idx_cite < 20):
            if hold_idx == -1 or idx_cite < hold_idx:
                hold_idx = idx_cite
                
        if idx_e200 != -1 and '\ue201' not in self.buffer[idx_e200:]:
            if hold_idx == -1 or idx_e200 < hold_idx:
                hold_idx = idx_e200
                
        if hold_idx != -1:
            output = self.buffer[:hold_idx]
            self.buffer = self.buffer[hold_idx:]
            return output
        else:
            output = self.buffer
            self.buffer = ""
            return output

    def flush(self) -> str:
        self.buffer = _RE_BRACKET_CITE.sub('', self.buffer)
        self.buffer = _RE_UNICODE_CITE.sub(self._replace_citations, self.buffer)
        self.buffer = _RE_OLD_CITE.sub(self._replace_citations, self.buffer)
        self.buffer = _RE_E200_STRIP.sub('', self.buffer)
        
        result = self.buffer.replace('\u200b', '')
        
        # Append references list if any
        if self.citations:
            valid_refs = []
            if self.ref_map:
                for ref_id, idx in sorted(self.ref_map.items(), key=lambda x: x[1]):
                    cite_info = self.citations.get(ref_id)
                    if cite_info and cite_info.get("url"):
                        name = cite_info.get('name') or cite_info['url']
                        valid_refs.append(f"[{idx}] [{name}]({cite_info['url']})")
            else:
                seen_urls = set()
                idx = 1
                for ref_id, cite_info in self.citations.items():
                    url = cite_info.get("url")
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        name = cite_info.get('name') or url
                        valid_refs.append(f"[{idx}] [{name}]({url})")
                        idx += 1
            
            if valid_refs:
                result += "\n\n### References\n" + "\n".join(valid_refs) + "\n"
                
        self.buffer = ""
        return result

def _stream(session, prompt: str, model: str, messages: list, conversation_id=None, plugins=None, files=None, api_key: str = ""):
    """Yield OpenAI ``chat.completion.chunk`` SSE events for ``prompt``.

    ``conversation_id`` continues an existing Copilot thread; ``None`` starts a
    fresh one (its id is emitted on the final chunk).
    """
    cid = new_id()
    created = int(time.time())
    
    # We delay yielding the initial role chunk until we successfully get the first item from Copilot.
    # This allows auth errors to propagate up before any SSE events are sent, enabling seamless session fallback.
    stream = session.client.stream(prompt, conversation_id=conversation_id, model=model, plugins=plugins, e5_attachments=files)
    
    stream_iter = iter(stream)
    try:
        first_item = next(stream_iter)
    except StopIteration:
        first_item = None
        
    yield sse_event(stream_chunk(cid, created, model, {"role": "assistant"}))
    
    try:
        final_text = ""
        final_thought = ""
        cleaner = StreamCleaner()
        
        def process_piece(piece):
            nonlocal final_text, final_thought
            if isinstance(piece, str) and piece:
                cleaned_piece = cleaner.process(piece)
                final_text += cleaned_piece
                if cleaned_piece:
                    return sse_event(stream_chunk(cid, created, model, {"content": cleaned_piece}))
            elif isinstance(piece, dict):
                if "thought" in piece:
                    final_thought += piece["thought"]
                    return sse_event(stream_chunk(cid, created, model, {"reasoning_content": piece["thought"]}))
                elif "citations" in piece:
                    cleaner.add_citations(piece["citations"])
            elif isinstance(piece, ImageResponse) and piece.url:
                markdown_img = f"\n\n![Generated Image]({piece.url})\n\n"
                final_text += markdown_img
                return sse_event(stream_chunk(cid, created, model, {"content": markdown_img}))
            return None

        if first_item:
            res = process_piece(first_item)
            if res: yield res

        for piece in stream_iter:
            res = process_piece(piece)
            if res: yield res
        
        # Flush any remaining text in the cleaner
        flushed = cleaner.flush()
        if flushed:
            final_text += flushed
            yield sse_event(stream_chunk(cid, created, model, {"content": flushed}))
        
        # Save the new state so future requests can continue seamlessly
        if stream.conversation_id:
            from .schemas import ChatMessage
            updated_messages = messages + [ChatMessage(role="assistant", content=final_text, reasoning_content=final_thought if final_thought else None)]
            new_head_hash = router._hash_messages(updated_messages)
            router.save_state(new_head_hash, stream.conversation_id, session.session_name, api_key=api_key)

        # Copilot's conversation id is known once the stream has run; emit it
        yield sse_event(
            stream_chunk(
                cid, created, model, {}, finish="stop",
                conversation_id=stream.conversation_id,
            )
        )
    except RateLimitExceeded as exc:
        secs = max(1, round(exc.wait_seconds))
        err_json = {
            "error": {
                "message": f"Rate limit exceeded. Retry in {secs}s.",
                "type": "rate_limit_error",
                "code": "rate_limit_exceeded"
            }
        }
        # SSE format for openai errors is sometimes yielded in the 'error' field or as a JSON body,
        # but since stream started, we just yield it as a pseudo message.
        yield sse_event(stream_chunk(cid, created, model, {"content": f"\n[error: rate limit exceeded, retry in {secs}s]"}, finish="error"))
    except Exception as exc:  # surface errors to the client instead of hanging
        import traceback, sys
        print(mask_token(f"[stream error] {traceback.format_exc()}"), file=sys.stderr)
        yield sse_event(
            stream_chunk(cid, created, model, {"content": "\n[error: upstream request failed]"}, finish="error")
        )
    yield "data: [DONE]\n\n"


def _stream_claude(session, prompt: str, model: str, messages: list, plugins=None, conversation_id=None, files=None, api_key: str = ""):
    """Yield Anthropic Claude SSE events for ``prompt``."""
    msg_id = new_msg_id()
    
    stream = session.client.stream(prompt, conversation_id=conversation_id, model=model, plugins=plugins, e5_attachments=files)
    
    stream_iter = iter(stream)
    try:
        first_item = next(stream_iter)
    except StopIteration:
        first_item = None

    yield stream_message_start(msg_id, model)
    yield stream_content_block_start(index=0)
    
    try:
        final_text = ""
        final_thought = ""
        cleaner = StreamCleaner()
        in_thought = False
        
        def process_piece(piece):
            nonlocal final_text, final_thought, in_thought
            res = []
            if isinstance(piece, str) and piece:
                if in_thought:
                    res.append(stream_content_block_delta(index=0, text="\n</thinking>\n\n"))
                    in_thought = False
                cleaned_piece = cleaner.process(piece)
                final_text += cleaned_piece
                if cleaned_piece:
                    res.append(stream_content_block_delta(index=0, text=cleaned_piece))
            elif isinstance(piece, dict):
                if "thought" in piece:
                    if not in_thought:
                        res.append(stream_content_block_delta(index=0, text="<thinking>\n"))
                        in_thought = True
                    final_thought += piece["thought"]
                    res.append(stream_content_block_delta(index=0, text=piece["thought"]))
                elif "citations" in piece:
                    cleaner.add_citations(piece["citations"])
            elif isinstance(piece, ImageResponse) and piece.url:
                if in_thought:
                    res.append(stream_content_block_delta(index=0, text="\n</thinking>\n\n"))
                    in_thought = False
                markdown_img = f"\n\n![Generated Image]({piece.url})\n\n"
                final_text += markdown_img
                res.append(stream_content_block_delta(index=0, text=markdown_img))
            return res

        if first_item:
            for r in process_piece(first_item): yield r

        for piece in stream_iter:
            for r in process_piece(piece): yield r
            
        flushed = cleaner.flush()
        if flushed:
            if in_thought:
                yield stream_content_block_delta(index=0, text="\n</thinking>\n\n")
                in_thought = False
            final_text += flushed
            yield stream_content_block_delta(index=0, text=flushed)
            
        if in_thought:
            yield stream_content_block_delta(index=0, text="\n</thinking>\n")

        if stream.conversation_id:
            from .schemas import ChatMessage
            updated_messages = messages + [ChatMessage(role="assistant", content=final_text, reasoning_content=final_thought if final_thought else None)]
            new_head_hash = router._hash_messages(updated_messages)
            router.save_state(new_head_hash, stream.conversation_id, session.session_name, api_key=api_key)

        yield stream_content_block_stop(index=0)
        yield stream_message_delta(stop_reason="end_turn")
        yield stream_message_stop()
        
    except RateLimitExceeded as exc:
        secs = max(1, round(exc.wait_seconds))
        yield stream_content_block_delta(index=0, text=f"\n[error: rate limit exceeded, retry in {secs}s]")
        yield stream_content_block_stop(index=0)
        yield stream_message_delta(stop_reason="error")
        yield stream_message_stop()
    except Exception as exc:
        import traceback, sys
        print(mask_token(f"[stream error] {traceback.format_exc()}"), file=sys.stderr)
        yield stream_content_block_delta(index=0, text="\n[error: upstream request failed]")
        yield stream_content_block_stop(index=0)
        yield stream_message_delta(stop_reason="error")
        yield stream_message_stop()



@app.get("/v1/models")
def list_models():
    from copilot.agent_registry import AGENT_REGISTRY
    return {
        "object": "list",
        "data": [
            {"id": model_id, "object": "model", "created": 0, "owned_by": "microsoft"}
            for model_id in AGENT_REGISTRY.keys()
        ],
    }


@app.post("/v1/chat/completions")
def chat_completions(req: ChatCompletionRequest, creds: HTTPAuthorizationCredentials = Depends(security)):
    api_key = creds.credentials if creds else None
    if not api_key:
        return JSONResponse(status_code=401, content={"error": {"message": "Missing API Key", "type": "authentication_error"}})

    try:
        conversation_id, prompt, _, preferred_session = router.route(
            api_key=api_key, 
            messages=req.messages, 
            client_provided_cid=req.conversation_id
        )
    except ValueError as e:
        return JSONResponse(status_code=401, content={"error": {"message": str(e), "type": "auth_error"}})

    try:
        files = extract_files(req.messages, last_turn_only=(conversation_id is not None))
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": {"message": str(e), "type": "invalid_request_error"}})

    if not prompt.strip():
        return JSONResponse(
            status_code=400,
            content={"error": {"message": "no text content in messages", "type": "invalid_request_error"}},
        )
    model = req.model or MODEL_NAME

    plugins = req.plugins or []
    if req.tools:
        for t in req.tools:
            if isinstance(t, dict):
                fn = t.get("function", {})
                name = fn.get("name") if isinstance(fn, dict) else None
                if name:
                    plugins.append(name)
            elif isinstance(t, str):
                plugins.append(t)
    if not plugins:
        plugins = None

    try:
        if req.stream:
            def stream_wrapper():
                nonlocal conversation_id
                nonlocal preferred_session
                
                # Try up to 3 different sessions from the pool if auth fails
                max_retries = 3
                for attempt in range(max_retries):
                    with pool.acquire_session(api_key, preferred_session=preferred_session) as session:
                        try:
                            if preferred_session and session.session_name != preferred_session:
                                # We fell back to a different account; Copilot conversation IDs are tied to accounts,
                                # so we must start a fresh conversation instead of failing.
                                conversation_id = None
                                
                            print(f"[API] Using session: {session.session_name} ({session.get_email()}) | conversation_id: {conversation_id}")
                            yield from _stream(session, prompt, model, req.messages, conversation_id, plugins, files=files, api_key=api_key)
                            return  # Success, exit retry loop
                        except RuntimeError as e:
                            if "Failed to decode oid/tid" in str(e) or "rate limit" in str(e).lower():
                                print(f"[API] Session fallback triggered (attempt {attempt+1}/{max_retries}) due to: {e}")
                                # Force this session to be unhealthy by removing its token, so the next acquire picks a different session
                                session.client.invalidate_auth()
                                # Clear preferred_session so pool.acquire_session is free to pick the next best healthy session
                                preferred_session = None
                                if attempt == max_retries - 1:
                                    raise
                                continue
                            raise
    
            return StreamingResponse(
                stream_wrapper(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
            )
        else:
            max_retries = 3
            reply = None
            for attempt in range(max_retries):
                with pool.acquire_session(api_key, preferred_session=preferred_session) as session:
                    try:
                        if preferred_session and session.session_name != preferred_session:
                            conversation_id = None
                        
                        print(f"[API] Using session: {session.session_name} ({session.get_email()}) | conversation_id: {conversation_id}")
                        reply = session.client.chat(prompt, conversation_id=conversation_id, model=model, plugins=plugins, e5_attachments=files)
                        break  # Success
                    except RuntimeError as e:
                        if "Failed to decode oid/tid" in str(e) or "rate limit" in str(e).lower():
                            print(f"[API] Session fallback triggered (attempt {attempt+1}/{max_retries}) due to: {e}")
                            session.client.invalidate_auth()
                            preferred_session = None
                            if attempt == max_retries - 1:
                                raise
                            continue
                        raise
            
            if reply is None:
                raise Exception("Failed to acquire reply after retries")
                
            final_text = reply.text
            if reply.images:
                for img in reply.images:
                    if img.url:
                        final_text += f"\n\n![Generated Image]({img.url})"
            
            if reply.conversation_id:
                from .schemas import ChatMessage
                updated_messages = req.messages + [ChatMessage(role="assistant", content=final_text)]
                new_head_hash = router._hash_messages(updated_messages)
                router.save_state(new_head_hash, reply.conversation_id, session.session_name, api_key=api_key)
            return completion_response(final_text, model, reply.conversation_id)
    except RateLimitExceeded as exc:
        secs = max(1, round(exc.wait_seconds))
        return JSONResponse(
            status_code=429,
            headers={"Retry-After": str(secs)},
            content={"error": {
                "message": f"Rate limit exceeded. Retry in {secs}s.",
                "type": "rate_limit_error",
                "code": "rate_limit_exceeded",
            }},
        )
    except Exception as exc:
        import traceback, sys
        print(mask_token(f"[chat error] {traceback.format_exc()}"), file=sys.stderr)
        return JSONResponse(
            status_code=502,
            content={"error": {"message": "upstream request failed", "type": "upstream_error"}},
        )


@app.post("/v1/messages")
def claude_messages(
    req: ClaudeMessageRequest, 
    creds: HTTPAuthorizationCredentials = Depends(security),
    x_api_key: str = Header(None)
):
    api_key = x_api_key or (creds.credentials if creds else None)
    if not api_key:
        return JSONResponse(status_code=401, content={"error": {"message": "Missing API Key", "type": "authentication_error"}})
    
    from .schemas import ChatMessage
    standard_messages = []
    if req.system:
        sys_text = req.system if isinstance(req.system, str) else "".join(b.get("text", "") for b in req.system if isinstance(b, dict))
        standard_messages.append(ChatMessage(role="system", content=sys_text))
        
    for m in req.messages:
        content = m.content if isinstance(m.content, str) else "".join(b.get("text", "") for b in m.content if isinstance(b, dict))
        standard_messages.append(ChatMessage(role=m.role, content=content))
        
    try:
        conversation_id, prompt, new_head_hash, preferred_session = router.route(
            api_key=api_key, 
            messages=standard_messages, 
            client_provided_cid=None
        )
    except ValueError as e:
        return JSONResponse(status_code=401, content={"error": {"message": str(e), "type": "authentication_error"}})

    try:
        files = extract_files(req.messages, last_turn_only=(conversation_id is not None))
    except ValueError as e:
        return JSONResponse(status_code=400, content={"type": "error", "error": {"type": "invalid_request_error", "message": str(e)}})

    if not prompt.strip():
        return JSONResponse(
            status_code=400,
            content={"error": {"message": "no text content", "type": "invalid_request_error"}},
        )
        
    model = req.model or MODEL_NAME

    if req.stream:
        def stream_wrapper():
            nonlocal preferred_session, conversation_id
            max_retries = 3
            for attempt in range(max_retries):
                with pool.acquire_session(api_key, preferred_session=preferred_session) as session:
                    try:
                        if preferred_session and session.session_name != preferred_session:
                            conversation_id = None
                            
                        print(f"[API] Using session: {session.session_name} ({session.get_email()}) | conversation_id: {conversation_id}")
                        yield from _stream_claude(session, prompt, model, standard_messages, plugins=None, conversation_id=conversation_id, files=files, api_key=api_key)
                        return
                    except RuntimeError as e:
                        if "Failed to decode oid/tid" in str(e) or "rate limit" in str(e).lower():
                            print(f"[API] Claude session fallback triggered (attempt {attempt+1}/{max_retries}) due to: {e}")
                            session.client.invalidate_auth()
                            preferred_session = None
                            if attempt == max_retries - 1:
                                raise
                            continue
                        raise

        return StreamingResponse(
            stream_wrapper(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )
    else:
        max_retries = 3
        reply = None
        for attempt in range(max_retries):
            with pool.acquire_session(api_key, preferred_session=preferred_session) as session:
                try:
                    if preferred_session and session.session_name != preferred_session:
                        conversation_id = None
                        
                    print(f"[API] Using session: {session.session_name} ({session.get_email()}) | conversation_id: {conversation_id}")
                    reply = session.client.chat(prompt, conversation_id=conversation_id, model=model, plugins=None, e5_attachments=files)
                    break
                except RuntimeError as e:
                    if "Failed to decode oid/tid" in str(e) or "rate limit" in str(e).lower():
                        print(f"[API] Claude session fallback triggered (attempt {attempt+1}/{max_retries}) due to: {e}")
                        session.client.invalidate_auth()
                        preferred_session = None
                        if attempt == max_retries - 1:
                            raise
                        continue
                    raise
                    
        if reply is None:
            return JSONResponse(status_code=500, content={"error": {"message": "upstream request failed", "type": "upstream_error"}})
            
        final_text = reply.text
        if reply.images:
            for img in reply.images:
                if img.url:
                    final_text += f"\n\n![Generated Image]({img.url})"
                    
        if reply.conversation_id:
            from .schemas import ChatMessage
            updated_messages = standard_messages + [ChatMessage(role="assistant", content=final_text)]
            new_head_hash = router._hash_messages(updated_messages)
            router.save_state(new_head_hash, reply.conversation_id, session.session_name, api_key=api_key)
                    
        return message_response(final_text, model)


def verify_admin(request: Request):
    expected_pass = os.environ.get("WEB_AUTH_PASSWORD")
    
    if not expected_pass:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server misconfigured: WEB_AUTH_PASSWORD is not set."
        )
    
    admin_auth = request.headers.get("X-Admin-Auth")
    if admin_auth is not None and secrets.compare_digest(admin_auth.encode("utf8"), expected_pass.encode("utf8")):
        return "admin"
        
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated or incorrect password"
    )


class AddKeyRequest(BaseModel):
    api_key: str
    sessions: list[str]

@app.get("/api/accounts", dependencies=[Depends(verify_admin)])
def get_accounts():
    return {
        "api_keys": pool.get_api_keys(),
        "sessions_info": pool.get_sessions_info()
    }

@app.post("/api/accounts", dependencies=[Depends(verify_admin)])
def add_account(req: AddKeyRequest):
    pool.add_api_key(req.api_key, req.sessions)
    return {"status": "ok"}

@app.delete("/api/accounts/{api_key}", dependencies=[Depends(verify_admin)])
def delete_account(api_key: str):
    pool.remove_api_key(api_key)
    return {"status": "ok"}

@app.delete("/api/accounts/{api_key}/{session_name}", dependencies=[Depends(verify_admin)])
def delete_session_from_key(api_key: str, session_name: str):
    pool.remove_session_from_key(api_key, session_name)
    return {"status": "ok"}

@app.get("/api/config", dependencies=[Depends(verify_admin)])
def get_config():
    return {"vnc_url": os.environ.get("VNC_URL", "")}

@app.post("/api/login", dependencies=[Depends(verify_admin)])
def login_session(session_name: str):
    """Triggers BrowserCopilot in a background thread for VNC interaction."""
    pool.add_session(session_name)
    
    def run_browser():
        try:
            # Ensure VNC is used if DISPLAY is set, else headless=False locally
            from copilot.browser import BrowserCopilot
            browser = BrowserCopilot(profile_dir=f"sessions/{session_name}/profile", headless=False)
            # The user interacts with VNC to login
            browser.login(path=f"sessions/{session_name}/token.json")
        except Exception as e:
            print(f"Browser login failed: {e}")
            
    import threading
    threading.Thread(target=run_browser, daemon=True).start()
    return {"status": "started", "session": session_name}


# Mount noVNC static files if available in the Docker container
if os.path.exists("/usr/share/novnc"):
    # We define the websocket route BEFORE the static files mount so it takes precedence
    @app.websocket("/novnc/vnc-ws")
    async def vnc_ws(websocket: WebSocket):
        expected_pass = os.environ.get("WEB_AUTH_PASSWORD")
        if not expected_pass:
            await websocket.close(code=1008)
            return
            
        token = (
            websocket.query_params.get("token")
            or websocket.query_params.get("pwd")
            or websocket.query_params.get("auth")
            or websocket.query_params.get("password")
            or websocket.headers.get("X-Admin-Auth")
            or websocket.cookies.get("admin_pwd")
            or websocket.cookies.get("token")
        )
        if not token or not secrets.compare_digest(token.encode("utf8"), expected_pass.encode("utf8")):
            print("[Security] Unauthorized attempt to connect to /novnc/vnc-ws")
            await websocket.close(code=1008)
            return

        # Dynamically accept the subprotocol requested by the client to prevent browser 1006 aborts
        client_protos = websocket.headers.get("sec-websocket-protocol", "")
        protos = [p.strip() for p in client_protos.split(",") if p.strip()]
        
        subprotocol = None
        if "binary" in protos:
            subprotocol = "binary"
        elif "base64" in protos:
            subprotocol = "base64"
        elif len(protos) > 0:
            subprotocol = protos[0]
            
        await websocket.accept(subprotocol=subprotocol)
        
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', 5900)
        except Exception as e:
            print(f"Failed to connect to local VNC server (5900): {e}")
            await websocket.close()
            return
            
        import base64
        async def ws_to_tcp():
            try:
                while True:
                    message = await websocket.receive()
                    if "bytes" in message and message["bytes"]:
                        writer.write(message["bytes"])
                        await writer.drain()
                    elif "text" in message and message["text"]:
                        writer.write(base64.b64decode(message["text"]))
                        await writer.drain()
                    elif message["type"] == "websocket.disconnect":
                        break
            except Exception as e:
                print(f"ws_to_tcp exception: {type(e).__name__} - {e}")

        async def tcp_to_ws():
            try:
                while True:
                    data = await reader.read(4096)
                    if not data:
                        break
                    if subprotocol == "base64":
                        await websocket.send_text(base64.b64encode(data).decode('ascii'))
                    else:
                        await websocket.send_bytes(data)
            except Exception as e:
                print(f"tcp_to_ws exception: {type(e).__name__} - {e}")
                
        t1 = asyncio.create_task(ws_to_tcp())
        t2 = asyncio.create_task(tcp_to_ws())
        
        try:
            done, pending = await asyncio.wait([t1, t2], return_when=asyncio.FIRST_COMPLETED)
            for p in pending:
                p.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            try:
                await websocket.close()
            except Exception:
                pass

    app.mount("/novnc", StaticFiles(directory="/usr/share/novnc"), name="novnc")

@app.get("/")
def root():
    index_path = os.path.join(os.path.dirname(__file__), "static", "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    return {"service": "Copilot OpenAI-compatible API", "endpoints": ["/v1/models", "/v1/chat/completions"]}
