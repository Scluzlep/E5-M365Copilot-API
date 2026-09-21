"""Signed-in session caching for the pure-HTTP path.

Bridges the interactive browser login to the headless :class:`copilot.client.Copilot`
driver: keeps a short-lived snapshot of cookies + MSAL access token on disk and
transparently refreshes it from the persistent browser profile when it goes stale.
"""

import json
import time
from pathlib import Path
from typing import Optional

from .atomic_write import write_text_atomic

# All session state (browser profile + cached auth) lives under one folder.
SESSION_DIR = "session"
DEFAULT_PROFILE_DIR = f"{SESSION_DIR}/profile"
DEFAULT_AUTH_FILE = f"{SESSION_DIR}/token.json"
# Microsoft access tokens live ~60-90 min; refresh well before that.
AUTH_MAX_AGE = 50 * 60


def reauth_with_sso(cached: dict, path: str = DEFAULT_AUTH_FILE) -> Optional[dict]:
    """Perform silent re-authentication using stored SSO cookies (ESTSAUTH / ESTSAUTHPERSISTENT).

    This implements M365Bridge's reauthWithSSO logic to bypass SPA 24h refresh token limits without launching Chromium.
    """
    sso_cookies = cached.get("sso_cookies") or cached.get("cookies") or {}
    cookie_parts = [f"{k}={v}" for k, v in sso_cookies.items() if k in ("ESTSAUTH", "ESTSAUTHPERSISTENT")]
    if not cookie_parts:
        return None

    import os
    import base64
    import hashlib
    import urllib.parse
    import requests

    print("[Auth] Attempting silent SSO cookie reauth (M365Bridge pattern)...")
    verifier_bytes = os.urandom(32)
    verifier = base64.urlsafe_b64encode(verifier_bytes).rstrip(b"=").decode("ascii")
    challenge_bytes = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(challenge_bytes).rstrip(b"=").decode("ascii")

    office_client = "4765445b-32c6-49b0-83e6-1d93765276ca"
    redirect_uri = "https://m365.cloud.microsoft/spalanding"
    scope = "https://substrate.office.com/sydney/.default openid profile offline_access"

    params = {
        "client_id": office_client,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": scope,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "sso_reload": "True",
    }
    authorize_url = f"https://login.microsoftonline.com/common/oauth2/v2.0/authorize?{urllib.parse.urlencode(params)}"
    headers = {"Cookie": "; ".join(cookie_parts)}

    try:
        session = requests.Session()
        current_url = authorize_url
        auth_code = None

        for _ in range(10):
            resp = session.get(current_url, headers=headers, allow_redirects=False, timeout=10)
            loc = resp.headers.get("Location")
            if not loc:
                break
            if "code=" in loc:
                parsed = urllib.parse.urlparse(loc)
                qs = urllib.parse.parse_qs(parsed.query or parsed.fragment)
                if "code" in qs and qs["code"]:
                    auth_code = qs["code"][0]
                    break
            current_url = loc

        if not auth_code:
            print("[Auth] Silent SSO cookie exchange did not yield auth code.")
            return None

        token_url = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
        token_data = {
            "grant_type": "authorization_code",
            "client_id": office_client,
            "code": auth_code,
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
        }
        token_headers = {
            "Origin": "https://www.office.com",
            "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
            "Cookie": "; ".join(cookie_parts),
        }
        token_resp = session.post(token_url, data=token_data, headers=token_headers, timeout=10)
        if token_resp.status_code == 200:
            token_json = token_resp.json()
            if "access_token" in token_json:
                cached["access_token"] = token_json["access_token"]
                cached["refresh_token"] = token_json.get("refresh_token", cached.get("refresh_token"))
                cached["saved_at"] = time.time()
                cached["login_at"] = time.time()
                write_text_atomic(path, json.dumps(cached, indent=2), durable=True)
                print("[Auth] Successfully refreshed access token via silent SSO cookies!")
                return cached
    except Exception as e:
        print(f"[Auth] Silent SSO cookie exchange error: {e}")
    return None


def load_auth(
    path: str = DEFAULT_AUTH_FILE,
    profile_dir: str = DEFAULT_PROFILE_DIR,
    max_age: int = AUTH_MAX_AGE,
    proxy: Optional[str] = None,
    auto_login: bool = True,
) -> dict:
    """Return ``{cookies, access_token, saved_at}`` for the signed-in user.

    Uses the cached snapshot at ``path`` while fresh; otherwise spins up a
    headless browser against the persistent ``profile_dir`` to read a fresh MSAL
    token (the profile stays signed in via its long-lived refresh token) and
    re-snapshots.

    When the profile is *not* signed in (e.g. first-ever use) and ``auto_login``
    is true, this opens a visible browser for interactive Microsoft sign-in
    instead of failing — so the very first call just works. Set
    ``auto_login=False`` (or run headless/CI) to get a ``RuntimeError`` instead.

    Intended for the pure-HTTP :class:`copilot.client.Copilot` path::

        auth = load_auth()
        Copilot().create_completion(..., cookies=auth["cookies"],
                                    access_token=auth["access_token"])
    """
    p = Path(path)
    cached = {}
    if p.exists():
        try:
            cached = json.loads(p.read_text(encoding="utf-8"))
            if cached.get("access_token") and (time.time() - cached.get("saved_at", 0)) < max_age:
                return cached
        except (ValueError, OSError):
            pass  # corrupt/unreadable -> refresh below

    # Attempt Pure API Refresh if we have a refresh_token
    import os
    rt = cached.get("refresh_token")
    
    # Seed from manual_rt.txt if available and not yet cached
    if not rt and os.path.exists("manual_rt.txt"):
        try:
            with open("manual_rt.txt", "r", encoding="utf-8") as f:
                rt = f.read().strip()
            print("Loaded Refresh Token from manual_rt.txt!")
        except Exception:
            pass

    # Skip API refresh if the session login is older than 23 hours to prevent SPA token expiration error
    is_expired_23h = False
    if cached:
        try:
            login_at = float(cached.get("login_at", cached.get("saved_at", 0) or 0))
            if time.time() - login_at > 23 * 3600:
                is_expired_23h = True
                print("[Auth] Session is older than 23 hours. Forcing silent SSO cookie exchange or BrowserCopilot headless renewal...")
        except (ValueError, TypeError):
            is_expired_23h = True
            print("[Auth] Invalid login_at/saved_at timestamp. Forcing renewal...")

    if rt and not is_expired_23h:
        import requests
        
        # Use OfficeHome Client ID to mint Copilot Scope
        office_client = "4765445b-32c6-49b0-83e6-1d93765276ca"
        url = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
        headers = {
            "Origin": "https://www.office.com"
        }
        data = {
            "grant_type": "refresh_token",
            "client_id": office_client,
            "refresh_token": rt,
            "scope": "https://substrate.office.com/sydney/.default openid profile offline_access"
        }
        
        try:
            resp = requests.post(url, data=data, headers=headers, timeout=10)
            if resp.status_code == 200:
                token_json = resp.json()
                if "access_token" in token_json:
                    cached["access_token"] = token_json["access_token"]
                    cached["refresh_token"] = token_json.get("refresh_token", rt) # Store the new RT!
                    cached["saved_at"] = time.time()
                    if "login_at" not in cached:
                        cached["login_at"] = cached.get("saved_at", time.time())
                    write_text_atomic(p, json.dumps(cached, indent=2), durable=True)
                    print("Token refreshed via Pure API!")
                    return cached
            else:
                print(f"Pure API refresh rejected: {resp.text}")
        except Exception as e:
            print(f"Pure API refresh request failed: {e}. Falling back to SSO/BrowserCopilot...")

    # Try silent SSO reauth before launching Chromium
    sso_result = reauth_with_sso(cached, path=path)
    if sso_result:
        return sso_result

    from .browser import BrowserCopilot

    # Fallback to headless BrowserCopilot if API refresh and SSO reauth failed (or no cookies)
    bot = BrowserCopilot(profile_dir=profile_dir, headless=True, proxy=proxy)
    try:
        bot.start()
        token = bot.acquire_chat_token()
        if token and not bot.region_blocked():
            return bot.export_auth(path=path, stamp=time.time(), login_stamp=time.time())
    finally:
        bot.close()

    # No signed-in session in the profile.
    if not auto_login:
        raise RuntimeError(
            "Not signed in (no access token in the browser profile). "
            "Run `python -m copilot login` and sign in first."
        )

    # First-time use: create the session interactively, then return its auth.
    print("No saved Copilot session found — opening a browser to sign in...")
    auth = BrowserCopilot(profile_dir=profile_dir, headless=False, proxy=proxy).login(path=path)
    if not auth.get("access_token"):
        raise RuntimeError(
            "Sign-in did not complete (no access token captured). "
            "Re-run and finish the Microsoft sign-in before pressing Enter, "
            "or sign in manually with `python -m copilot login`."
        )
    return auth
