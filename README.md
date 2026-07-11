# E5 M365 Copilot API: Enterprise-Grade Substrate Bridge & Dual-Protocol Gateway

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.109+-00a393.svg)](https://fastapi.tiangolo.com)
[![OpenAI & Claude Compatible](https://img.shields.io/badge/Protocol-OpenAI%20%7C%20Anthropic%20Claude-purple.svg)]()

**Turn your free Microsoft Copilot / E5 Substrate account into an enterprise-grade, highly concurrent, dual-protocol LLM gateway.**

**E5 M365 Copilot API** is an advanced reverse-engineered bridge that transforms [copilot.microsoft.com](https://copilot.microsoft.com) and Microsoft 365 Substrate into a standard **OpenAI (`/v1/chat/completions`)** and **Anthropic Claude (`/v1/messages`)** compatible API. Designed for high concurrency, multi-tenant isolation, and zero-maintenance operation, it incorporates advanced reverse-engineering insights (including double-frame telemetry, silent SSO re-authentication, and incremental delta pruning).

---

## ✨ Why E5-M365Copilot-API? (Key Breakthroughs)

- 🔄 **Dual-Protocol Drop-In Compatibility**
  - Speaks both **OpenAI Chat Completions** and **Anthropic Claude Messages** (`/v1/messages`) APIs natively.
  - Seamlessly streams Chain-of-Thought reasoning (`reasoning_content` / `<thinking>`) and handles Anthropic-specific SSE content block transitions.
  - Direct model alias resolution (`claude-3-5-sonnet`, `claude-3-opus`, `gpt-4o`, `copilot`) via intelligent agent registry.

- ⚡ **Multi-Account Session Pool & Concurrency Engine**
  - **Thread-Safe Pooling (`server/accounts.py`)**: Bind multiple Microsoft/Google accounts to specific API keys. The engine automatically rotates and load-balances across healthy sessions.
  - **Compound Routing & Isolation (`api_key:session_name:hash`)**: Guarantees strict multi-tenant conversation isolation across multiple users and threads.
  - **Token Bucket Rate Limiting**: Built-in per-session rate limiters (`RATE_LIMIT_RPM`, `RATE_LIMIT_BURST`) prevent account throttling.

- 🛡️ **Self-Healing & Protocol Mastery (`driver.py` & `api.py`)**
  - **Double-Frame Telemetry Closed-Loop (`\x1E`)**: Strictly unpacks and sends paired `Chat` + `Metrics` telemetry frames required by Microsoft E5 Substrate, eliminating backend disconnects.
  - **`Disengaged` Circuit Breaker (502 Self-Healing)**: Automatically detects Copilot `Disengaged` disconnection signals, discards stale `conversation_id`s, retries seamlessly on fresh sockets, and triggers a clean 502 circuit breaker if upstream persists.
  - **Incremental Delta Pruning (`_slice_delta_prompt`)**: Automatically slices multi-turn history from the last `assistant` turn, sending only system prompts plus the latest user delta to dramatically reduce E5 token overhead and latency.

- 🔐 **24-Hour Silent SSO Re-Authentication (`reauth_with_sso`)**
  - Captures `ESTSAUTH` and `ESTSAUTHPERSISTENT` cookies directly from `login.microsoftonline.com` during initial login.
  - When access tokens expire, the background engine silently renews them against the M365 Substrate OAuth exchange (`sso_reload=True`) **without opening browser windows or interrupting API requests**.

- 🎨 **Multi-Modal Image Generation & Editing (`/v1/images/*`)**
  - Full support for **`/v1/images/generations`** and **`/v1/images/edits`**.
  - Accepts image prompts and input attachments, automatically parses Copilot Designer output URLs, and supports both `response_format="url"` and `response_format="b64_json"`.

- 🧹 **Trace-Free UI & Warmup Cleanup (`_CDP_DELETE_MSG_JS`)**
  - When minting chat tokens via warmup greetings (`"hi"`), the browser engine immediately executes DOM/CDP cleanup scripts (`button[aria-label*='Delete']`) to silently delete the test message from your account history.

- 🔒 **Enterprise Security & SSRF Protection**
  - Built-in `SSRFProtectedSession` actively intercepts requests pointing to internal metadata services (`169.254.169.254`, `127.0.0.1`, `10.0.0.0/8`, etc.).
  - All logs strictly redact sensitive OAuth tokens, refresh codes, and session credentials (`mask_token`).

---

## 📋 Table of Contents

- [Requirements](#requirements)
- [Setup (2 Minutes)](#setup-2-minutes)
- [Running with Docker & noVNC](#running-with-docker--novnc)
- [Supported API Endpoints](#supported-api-endpoints)
- [Usage Guide](#usage-guide)
  - [1. OpenAI SDK Drop-In](#1-openai-sdk-drop-in)
  - [2. Anthropic Claude SDK Drop-In](#2-anthropic-claude-sdk-drop-in)
  - [3. Python Native Client (`CopilotClient`)](#3-python-native-client-copilotclient)
  - [4. Multi-Modal Image Generation & Editing](#4-multi-modal-image-generation--editing)
- [Multi-Account Pool & Admin Management](#multi-account-pool--admin-management)
- [Configuration & Environment Variables](#configuration--environment-variables)
- [Troubleshooting & Diagnostics](#troubleshooting--diagnostics)
- [License & Star History](#license--star-history)

---

## 💻 Requirements

- **Python 3.9+**
- A **Microsoft Account** (Free personal account, M365 E5 Substrate, or Google federated login)
- Works natively on **Windows, macOS, Linux, and Docker**

---

## 🚀 Setup (2 Minutes)

### 1. Clone & Activate Environment

On **macOS / Linux**:
```bash
git clone <your-repo-url> E5-M365Copilot-API
cd E5-M365Copilot-API
python3 -m venv venv
source venv/bin/activate
```

On **Windows (PowerShell)**:
```powershell
git clone <your-repo-url> E5-M365Copilot-API
cd E5-M365Copilot-API
python -m venv venv
venv\Scripts\Activate.ps1
```

### 2. Install Dependencies & Browser Drivers

```bash
pip install -r requirements.txt
playwright install chromium
```

### 3. Sign In Once

```bash
python -m copilot login
```

A visible browser window will open at `copilot.microsoft.com`. Sign in to your Microsoft or Google account. Once signed in, the window **closes automatically**. The engine captures your session, SSO cookies, and chat tokens into `session/` (`token.json`).

---

## 🐳 Running with Docker & noVNC

Run the gateway in Docker with embedded **noVNC** support, allowing you to perform interactive logins remotely via a web browser!

```bash
docker compose up --build
```

- **OpenAI / Claude API Gateway**: `http://localhost:8000/v1`
- **Web Admin & noVNC Login Portal**: `http://localhost:8000/` (or `/novnc/vnc.html`)

> **Remote VNC Login:** If you run Docker on a headless Linux server/VPS, visit `http://<your-vps-ip>:8000/` and click **"Add Account"** or **"Interactive VNC Login"**. Enter your `WEB_AUTH_PASSWORD` to control the embedded Chromium browser directly from your web tab!

---

## 🔌 Supported API Endpoints

| Method | Endpoint | Protocol | Description |
| :--- | :--- | :--- | :--- |
| `POST` | `/v1/chat/completions` | OpenAI | Full Chat Completions (`stream: true/false`, function/tool synthesis, image outputs) |
| `POST` | `/v1/messages` | Anthropic Claude | Claude Messages API (`stream: true/false`, `reasoning_content` mapping) |
| `POST` | `/v1/images/generations` | OpenAI Images | Generate high-quality images via Copilot Designer (`url` / `b64_json`) |
| `POST` | `/v1/images/edits` | OpenAI Images | Modify/edit images based on text prompts and base64/URL image inputs |
| `GET` | `/v1/models` | OpenAI / Claude | Lists available agent models and aliases |
| `GET/POST` | `/api/accounts` | Admin Management | Manage multi-account session bindings and API keys (protected by `WEB_AUTH_PASSWORD`) |

---

## 📖 Usage Guide

Start the local server:
```bash
python app.py
# Server listening on http://127.0.0.1:8000
```

### 1. OpenAI SDK Drop-In

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="sk-your-configured-key"  # Or "unused" if no pool keys configured
)

# Multi-turn streaming chat with reasoning
response = client.chat.completions.create(
    model="copilot",  # or "gpt-4o", "claude-3-5-sonnet"
    messages=[
        {"role": "system", "content": "You are a helpful coding expert."},
        {"role": "user", "content": "Explain async recursion in Python."}
    ],
    stream=True
)

for chunk in response:
    # Print thought block if present
    if hasattr(chunk.choices[0].delta, "reasoning_content") and chunk.choices[0].delta.reasoning_content:
        print(f"[{chunk.choices[0].delta.reasoning_content}]", end="")
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="", flush=True)
```

### 2. Anthropic Claude SDK Drop-In

```python
import anthropic

client = anthropic.Anthropic(
    base_url="http://localhost:8000",
    api_key="sk-your-configured-key"
)

with client.messages.stream(
    max_tokens=1024,
    messages=[{"role": "user", "content": "Write a haiku about artificial intelligence."}],
    model="claude-3-5-sonnet-20241022",
) as stream:
    for text in stream.text_stream:
        print(text, end="", flush=True)
```

### 3. Python Native Client (`CopilotClient`)

For lightweight scripts without running the local server:

```python
from copilot import CopilotClient

client = CopilotClient()  # Automatically loads session from session/

# Direct multi-turn conversation
reply1 = client.chat("Hello! My name is Alice.")
print(f"Copilot: {reply1.text} (Conversation ID: {reply1.conversation_id})")

reply2 = client.chat("What is my name?", conversation_id=reply1.conversation_id)
print(f"Copilot: {reply2.text}")

# Streaming
for chunk in client.stream("Tell me a quick joke."):
    print(chunk, end="", flush=True)
```

### 4. Multi-Modal Image Generation & Editing

Generate or edit images using the official OpenAI `images` SDK:

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8000/v1", api_key="sk-any")

# Image Generation
gen_response = client.images.generate(
    prompt="A futuristic cyber-punk cityscape at sunset with neon rain, 8k resolution",
    n=1,
    response_format="url"
)
print("Generated Image URL:", gen_response.data[0].url)

# Image Editing (Base64 or URL input)
edit_response = client.images.edit(
    image=open("input_image.png", "rb"),
    prompt="Add a glowing golden dragon flying between the skyscrapers",
    response_format="b64_json"
)
# edit_response.data[0].b64_json contains the base64 encoded png
```

---

## 🏢 Multi-Account Pool & Admin Management

When running under high load or multi-user enterprise scenarios, a single Microsoft account can be throttled. E5-M365Copilot-API solves this with `server/accounts.py`:

1. **Sign in multiple accounts** into different session directories (e.g. `sessions/work_account`, `sessions/personal_account`).
2. **Bind them to API Keys** via `sessions/config.json` (or via the `/api/accounts` REST endpoint):
   ```json
   {
     "api_keys": {
       "sk-team-alpha-key-2026": ["work_account", "personal_account"],
       "sk-dev-user-key-2026": ["personal_account"]
     }
   }
   ```
3. When requests arrive with `Authorization: Bearer sk-team-alpha-key-2026`, the server **load-balances across healthy accounts**, monitors rate limits (`TokenBucket`), and automatically rotates if an account triggers `502` or `Disengaged`.

---

## ⚙️ Configuration & Environment Variables

You can customize server behavior using environment variables or a `.env` file:

| Environment Variable | Default | Description |
| :--- | :--- | :--- |
| `HOST` | `0.0.0.0` | Server host interface |
| `PORT` | `8000` | Server listening port |
| `WEB_AUTH_PASSWORD` | *(None)* | **Required for Admin & noVNC access.** Password to secure `/api/accounts` and `/novnc/vnc-ws` |
| `RATE_LIMIT_RPM` | `12` | Maximum sustained requests per minute per session (`0` disables rate limiting) |
| `RATE_LIMIT_BURST` | `4` | Maximum back-to-back instant burst requests allowed before throttling |
| `HTTP_PROXY` / `HTTPS_PROXY` | *(None)* | Proxy URL for Chromium login and HTTP/Socket.IO connections |

---

## 🛠️ Troubleshooting & Diagnostics

If your session experiences disconnection or login issues, run the built-in diagnostic and self-healing suite:

```bash
python tests/diagnostic.py                # Runs browser test + generates clean report
python tests/diagnostic.py --report-only  # Headless mode: report only without opening window
```

What `diagnostic.py` does:
1. **Refreshes Credentials**: Launches Chromium on `session/profile/`, renews Microsoft Substrate cookies (`ESTSAUTH`), and refreshes `token.json`.
2. **Captures Protocol Trace**: Records live WebSocket framing (`setOptions` → `Chat` + `Metrics` telemetry → `appendText`) to `session/ws_capture.log`.
3. **Generates Redacted Report**: Writes `session/diagnostic_report.txt` containing health metrics, token lengths, and live probes. **All tokens, cookies, and personal emails are strictly masked and redacted**, making the report 100% safe to attach to GitHub issues.

---

## 📁 Project Structure

```text
E5-M365Copilot-API/
├── copilot/
│   ├── driver.py           # Substrate WebSocket driver, double-frame telemetry, SSE parser
│   ├── browser.py          # Playwright Chromium manager, SSO cookie extraction, UI warmup cleanup
│   ├── auth.py             # 24-hour silent SSO reauth (ESTSAUTH broker) & Pure API token renewal
│   ├── models.py           # Pydantic data schemas for messages, attachments, and image outputs
│   ├── agent_registry.py   # Model alias registry (OpenAI / Claude / Substrate options sets)
│   └── utils.py            # SSRF protection, token redaction (`mask_token`), and logging utilities
├── server/
│   ├── api.py              # FastAPI application, OpenAI / Claude routes, Disengaged circuit breakers
│   ├── router.py           # Multi-turn conversation state router & incremental delta pruning
│   ├── accounts.py         # Multi-account thread-safe session pool, rate limiters (`TokenBucket`)
│   ├── prompt.py           # Context formatting, tool-call synthesis, and file attachment extraction
│   └── claude_format.py    # Anthropic Claude SSE event and JSON response generators
├── tests/
│   ├── test_router.py      # Conversation state LRU eviction and compound routing tests
│   ├── test_routing.py     # Agent registry model matching and case-insensitivity tests
│   ├── test_regression.py  # Telemetry framing, attachment limits, and token masking tests
│   └── test_component3.py  # Delta pruning, synthetic tools, and image endpoint validation tests
├── app.py                  # Entry point to launch the uvicorn FastAPI server
├── requirements.txt        # Python dependency declarations
└── docker-compose.yml      # Multi-stage Docker container build with embedded noVNC portal
```

---

## 📜 License & Star History

Released under the [MIT License](LICENSE). 

> **Disclaimer:** This is an independent, open-source project and is not affiliated with, endorsed by, or sponsored by Microsoft Corporation or Anthropic. It automates authorized user access to Microsoft 365 Copilot web endpoints. Please use responsibly and in accordance with Microsoft's terms of service.

<a href="https://www.star-history.com/?repos=sums001%2FWindows-Copilot-API&type=timeline&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=sums001/Windows-Copilot-API&type=timeline&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=sums001/Windows-Copilot-API&type=timeline&legend=top-left" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=sums001/Windows-Copilot-API&type=timeline&legend=top-left" />
 </picture>
</a>
