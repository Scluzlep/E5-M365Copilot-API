"""Microsoft 365 Native App PKCE Authorization & Token Exchange.

Eliminates the need for headless browsers, Playwright, or DOM scraping for M365 login.
Uses the official Microsoft Native Public Client ID to obtain a sliding refresh_token
which allows pure HTTP token renewal indefinitely.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import secrets
import time
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlencode, urlsplit

import requests

_log = logging.getLogger(__name__)

M365_NATIVE_CLIENT_ID = "c0ab8ce9-e9a0-42e7-b064-33d422df41f1"
NATIVE_REDIRECT_URI = "https://login.microsoftonline.com/common/oauth2/nativeclient"
PKCE_SCOPE = "https://substrate.office.com/sydney/.default offline_access openid profile"

_AUTHORIZE_URL = "https://login.microsoftonline.com/{authority}/oauth2/v2.0/authorize"
_TOKEN_URL = "https://login.microsoftonline.com/{authority}/oauth2/v2.0/token"
_HTTP_TIMEOUT_SECONDS = 30.0

# In-memory store for pending PKCE verifiers: {session_id: (verifier, timestamp)}
_PENDING_VERIFIERS: Dict[str, Tuple[str, float]] = {}
PENDING_TTL_SECONDS = 15 * 60


def make_verifier() -> str:
    """Generate RFC 7636 code_verifier."""
    return base64.urlsafe_b64encode(secrets.token_bytes(64)).decode("ascii").rstrip("=")


def code_challenge(verifier: str) -> str:
    """Generate RFC 7636 S256 code_challenge."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def extract_code_from_redirect_url(url: str) -> str:
    """Extract authorization code from the redirected address bar URL."""
    cleaned = url.strip()
    # Handle if user pasted just the code itself or query string
    if "?" in cleaned or "#" in cleaned:
        parsed = urlsplit(cleaned)
        code = parse_qs(parsed.query).get("code", [""])[0]
        if not code and parsed.fragment:
            code = parse_qs(parsed.fragment).get("code", [""])[0]
        return code
    return cleaned


def start_pkce_login(
    session_id: str,
    authority: str = "common",
) -> Dict[str, str]:
    """Start an interactive PKCE flow and return the Microsoft authorize URL."""
    # Prune expired verifiers
    now = time.time()
    for s_id, (_, ts) in list(_PENDING_VERIFIERS.items()):
        if now - ts > PENDING_TTL_SECONDS:
            _PENDING_VERIFIERS.pop(s_id, None)

    verifier = make_verifier()
    challenge = code_challenge(verifier)
    _PENDING_VERIFIERS[session_id] = (verifier, now)

    auth_url_base = _AUTHORIZE_URL.format(authority=authority or "common")
    query_params = {
        "client_id": M365_NATIVE_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": NATIVE_REDIRECT_URI,
        "response_mode": "query",
        "scope": PKCE_SCOPE,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "prompt": "select_account",
    }
    authorize_url = f"{auth_url_base}?{urlencode(query_params)}"

    return {
        "session_id": session_id,
        "authorize_url": authorize_url,
        "redirect_uri": NATIVE_REDIRECT_URI,
    }


async def exchange_pkce_code(
    session_id: str,
    redirect_url_or_code: str,
    authority: str = "common",
) -> Dict[str, Any]:
    """Exchange code from redirect URL for access_token and refresh_token."""
    pending = _PENDING_VERIFIERS.pop(session_id, None)
    if not pending:
        raise ValueError(f"No pending PKCE login found for session '{session_id}' (or it has expired). Please start login again.")

    verifier, _ = pending
    code = extract_code_from_redirect_url(redirect_url_or_code)
    if not code:
        raise ValueError("Could not extract authorization code from the provided URL. Please paste the full redirected URL.")

    token_url = _TOKEN_URL.format(authority=authority or "common")
    payload = {
        "client_id": M365_NATIVE_CLIENT_ID,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": NATIVE_REDIRECT_URI,
        "code_verifier": verifier,
        "scope": PKCE_SCOPE,
    }

    def _do_exchange():
        return requests.post(token_url, data=payload, timeout=_HTTP_TIMEOUT_SECONDS)

    resp = await asyncio.to_thread(_do_exchange)
    try:
        data = resp.json()
    except Exception:
        data = {}

    if resp.status_code != 200 or "access_token" not in data:
        error_desc = data.get("error_description") or data.get("error") or resp.text
        raise RuntimeError(f"Microsoft OAuth exchange failed ({resp.status_code}): {error_desc}")

    return {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token"),
        "expires_in": data.get("expires_in", 3600),
        "token_type": data.get("token_type", "Bearer"),
        "client_id": M365_NATIVE_CLIENT_ID,
    }


async def refresh_m365_token(
    refresh_token: str,
    authority: str = "common",
) -> Dict[str, Any]:
    """Pure HTTP sliding refresh using refresh_token against Native Client."""
    token_url = _TOKEN_URL.format(authority=authority or "common")
    payload = {
        "client_id": M365_NATIVE_CLIENT_ID,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "scope": PKCE_SCOPE,
    }

    def _do_refresh():
        return requests.post(token_url, data=payload, timeout=_HTTP_TIMEOUT_SECONDS)

    resp = await asyncio.to_thread(_do_refresh)
    try:
        data = resp.json()
    except Exception:
        data = {}

    if resp.status_code != 200 or "access_token" not in data:
        error_desc = data.get("error_description") or data.get("error") or resp.text
        raise RuntimeError(f"Token refresh failed ({resp.status_code}): {error_desc}")

    return {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token") or refresh_token,
        "expires_in": data.get("expires_in", 3600),
    }
