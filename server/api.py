"""FastAPI app wiring Copilot onto the OpenAI Chat Completions API."""

import threading
import time

from fastapi import FastAPI, Depends
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from copilot.models import ImageResponse

from .config import MODEL_NAME, RATE_LIMIT_BURST, RATE_LIMIT_RPM
from .openai_format import (
    completion_response,
    new_id,
    sse_event,
    stream_chunk,
)
from .prompt import messages_to_prompt
from .ratelimit import TokenBucket
from .schemas import ChatCompletionRequest
from .router import router

app = FastAPI(title="Copilot OpenAI-compatible API", version="1.0.0")
security = HTTPBearer(auto_error=False)

_AUTH_HELP = (
    "Copilot authentication failed or token expired. "
    "Please re-login: run `python -m copilot login` or ensure your session/profile has a valid E5 login."
)

# Self-imposed rate limit on top of the concurrency lock below: this caps
# requests-per-minute, the lock caps requests-in-flight. See server/ratelimit.py.
_rate_limiter = TokenBucket(RATE_LIMIT_RPM, RATE_LIMIT_BURST)


def _rate_limited_response():
    """Spend a token; return an OpenAI-shaped 429 if none left, else ``None``."""
    allowed, wait = _rate_limiter.try_acquire()
    if allowed:
        return None
    secs = max(1, round(wait))
    return JSONResponse(
        status_code=429,
        headers={"Retry-After": str(secs)},
        content={"error": {
            "message": (
                f"Rate limit exceeded (>{RATE_LIMIT_RPM:g} req/min). "
                f"Retry in {secs}s."
            ),
            "type": "rate_limit_error",
            "code": "rate_limit_exceeded",
        }},
    )

# (Locks are now managed per-account in server/accounts.py)


from .accounts import pool

def _stream(api_key: str, prompt: str, model: str, messages: list, conversation_id=None, plugins=None):
    """Yield OpenAI ``chat.completion.chunk`` SSE events for ``prompt``.

    ``conversation_id`` continues an existing Copilot thread; ``None`` starts a
    fresh one (its id is emitted on the final chunk).
    """
    cid = new_id()
    created = int(time.time())
    try:
        with pool.acquire_session(api_key) as session:  # non-blocking fallback to available session
            yield sse_event(stream_chunk(cid, created, model, {"role": "assistant"}))
            stream = session.client.stream(prompt, conversation_id=conversation_id, model=model, plugins=plugins)
            final_text = ""
            for piece in stream:
                if isinstance(piece, str) and piece:
                    final_text += piece
                    yield sse_event(stream_chunk(cid, created, model, {"content": piece}))
                elif isinstance(piece, ImageResponse) and piece.url:
                    markdown_img = f"\n\n![Generated Image]({piece.url})\n\n"
                    final_text += markdown_img
                    yield sse_event(stream_chunk(cid, created, model, {"content": markdown_img}))
            
            # Save the new state so future requests can continue seamlessly
            if stream.conversation_id:
                from .schemas import ChatMessage
                updated_messages = messages + [ChatMessage(role="assistant", content=final_text)]
                new_head_hash = router._hash_messages(updated_messages)
                router.save_state(new_head_hash, stream.conversation_id)

            # Copilot's conversation id is known once the stream has run; emit it
            yield sse_event(
                stream_chunk(
                    cid, created, model, {}, finish="stop",
                    conversation_id=stream.conversation_id,
                )
            )
    except Exception as exc:  # surface errors to the client instead of hanging
        import traceback, sys
        print(f"[stream error] {traceback.format_exc()}", file=sys.stderr)
        yield sse_event(
            stream_chunk(cid, created, model, {"content": "\n[error: upstream request failed]"}, finish="error")
        )
    yield "data: [DONE]\n\n"


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
    api_key = creds.credentials if creds else "sk-default"
    
    try:
        conversation_id, prompt, _ = router.route(
            api_key=api_key, 
            messages=req.messages, 
            client_provided_cid=req.conversation_id
        )
    except ValueError as e:
        return JSONResponse(status_code=401, content={"error": {"message": str(e), "type": "auth_error"}})

    if not prompt.strip():
        return JSONResponse(
            status_code=400,
            content={"error": {"message": "no text content in messages", "type": "invalid_request_error"}},
        )
    model = req.model or MODEL_NAME

    # Enforce the per-minute ceiling before touching the upstream lock, so excess
    # callers get a fast 429 instead of piling up behind the serialized queue.
    limited = _rate_limited_response()
    if limited is not None:
        return limited

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

    if req.stream:
        return StreamingResponse(
            _stream(api_key, prompt, model, req.messages, conversation_id, plugins=plugins), media_type="text/event-stream"
        )

    try:
        with pool.acquire_session(api_key) as session:
            reply = session.client.chat(prompt, conversation_id=conversation_id, model=model, plugins=plugins)
            final_text = reply.text
            if reply.images:
                for img in reply.images:
                    if img.url:
                        final_text += f"\n\n![Generated Image]({img.url})"
            
            if reply.conversation_id:
                from .schemas import ChatMessage
                updated_messages = req.messages + [ChatMessage(role="assistant", content=final_text)]
                new_head_hash = router._hash_messages(updated_messages)
                router.save_state(new_head_hash, reply.conversation_id)
            return completion_response(final_text, model, reply.conversation_id)
    except Exception as exc:
        import traceback, sys
        print(f"[chat error] {traceback.format_exc()}", file=sys.stderr)
        return JSONResponse(
            status_code=502,
            content={"error": {"message": "upstream request failed", "type": "upstream_error"}},
        )


from fastapi import BackgroundTasks, Depends, HTTPException, status, WebSocket
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import asyncio
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import secrets
import os

security_basic = HTTPBasic(auto_error=False)

def verify_admin(credentials: HTTPBasicCredentials = Depends(security_basic)):
    if os.environ.get("WEB_AUTH_ENABLED", "true").lower() in ("false", "0", "no"):
        return True
        
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Basic"},
        )

    expected_user = os.environ.get("WEB_AUTH_USERNAME", "admin")
    expected_pass = os.environ.get("WEB_AUTH_PASSWORD", "copilot")
    
    correct_username = secrets.compare_digest(credentials.username.encode("utf8"), expected_user.encode("utf8"))
    correct_password = secrets.compare_digest(credentials.password.encode("utf8"), expected_pass.encode("utf8"))
    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username

class AddKeyRequest(BaseModel):
    api_key: str
    sessions: list[str]

@app.get("/api/accounts", dependencies=[Depends(verify_admin)])
def get_accounts():
    return {"api_keys": pool.get_api_keys()}

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
def login_session(session_name: str, background_tasks: BackgroundTasks):
    """Triggers BrowserCopilot in a background thread for VNC interaction."""
    pool.add_session(session_name)
    
    def run_browser():
        try:
            import os
            # Ensure VNC is used if DISPLAY is set, else headless=False locally
            from copilot.browser import BrowserCopilot
            browser = BrowserCopilot(profile_dir=f"sessions/{session_name}/profile", headless=False)
            # The user interacts with VNC to login
            browser.login(path=f"sessions/{session_name}/token.json")
        except Exception as e:
            print(f"Browser login failed: {e}")
            
    background_tasks.add_task(run_browser)
    return {"status": "started", "session": session_name}

@app.websocket("/vnc-ws")
async def vnc_ws(websocket: WebSocket):
    # Accept the connection with the binary subprotocol expected by noVNC
    await websocket.accept(subprotocol="binary")
    
    try:
        reader, writer = await asyncio.open_connection('127.0.0.1', 5900)
    except Exception as e:
        print(f"Failed to connect to local VNC server (5900): {e}")
        await websocket.close()
        return

    async def ws_to_tcp():
        try:
            while True:
                data = await websocket.receive_bytes()
                writer.write(data)
                await writer.drain()
        except Exception:
            pass

    async def tcp_to_ws():
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                await websocket.send_bytes(data)
        except Exception:
            pass

    t1 = asyncio.create_task(ws_to_tcp())
    t2 = asyncio.create_task(tcp_to_ws())
    
    done, pending = await asyncio.wait([t1, t2], return_when=asyncio.FIRST_COMPLETED)
    for p in pending:
        p.cancel()
    
    writer.close()
    await writer.wait_closed()
    await websocket.close()

import os
# Mount noVNC static files if available in the Docker container
if os.path.exists("/usr/share/novnc"):
    app.mount("/novnc", StaticFiles(directory="/usr/share/novnc"), name="novnc")

@app.get("/", dependencies=[Depends(verify_admin)])
def root():
    index_path = os.path.join(os.path.dirname(__file__), "static", "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    return {"service": "Copilot OpenAI-compatible API", "endpoints": ["/v1/models", "/v1/chat/completions"]}
