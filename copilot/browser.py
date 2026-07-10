"""Browser-backed sign-in and chat-token capture for Microsoft 365 E5 Substrate.

Playwright support for the pure-HTTP :class:`copilot.client.CopilotClient`: it does NOT
chat. Its sole job is to establish and refresh the signed-in session that the
HTTP driver runs on — interactive Microsoft login plus capture of the Copilot chat token.

``BrowserCopilot`` launches a **persistent** Playwright Chromium profile so that
sign-in sessions survive restarts.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
import os
from urllib.parse import parse_qs, urlparse

# Set local browser path in workspace (.browsers) so browsers aren't installed to AppData
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(Path(__file__).resolve().parent.parent / ".browsers"))

from playwright.sync_api import sync_playwright, Error as PlaywrightError

from .auth import DEFAULT_AUTH_FILE, DEFAULT_PROFILE_DIR
from .useragent import CHROME_UA, US_LOCALE, US_TIMEZONE, US_ACCEPT_LANGUAGE

COPILOT_URL = "https://m365.cloud.microsoft/chat"

# The one UA every browser context advertises — the same string the curl_cffi
# driver presents (see copilot/useragent.py). Applied to *both* headless and
# visible launches so client hints match. It also hides
# the "HeadlessChrome/..." token headless Chromium otherwise leaks. Because
# CHROME_UA tracks Playwright's bundled Chromium version, the override doesn't
# contradict the browser's native Sec-CH-UA client hint.
_STEALTH_UA = CHROME_UA

# Injected into every frame to hide the residual automation tell that survives
# --disable-blink-features=AutomationControlled in some Chromium builds.
_STEALTH_INIT_JS = (
    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
)

# --- in-page JavaScript -----------------------------------------------------

# Discover the Copilot chat MSAL access token from localStorage. The cache holds
# several tokens for different scopes; the chat WebSocket only accepts the one
# scoped 'ChatAI.ReadWrite' — a wrong-audience token (e.g. the Graph
# User.Read/Files.Read token) makes the WS upgrade 401. We therefore PREFER the
# ChatAI token and only fall back to the first token found if none matches.
# Returns null for anonymous sessions (anonymous chat may still work via cookies).
_FIND_TOKEN_JS = """
() => {
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      const v = localStorage.getItem(k);
      if (v && v.indexOf('"credentialType":"AccessToken"') !== -1) {
        try {
          const o = JSON.parse(v);
          if (o && o.secret) {
            // Match the chat scope (e.g. '<resource>/ChatAI.ReadWrite').
            // STRICT REQUIREMENT: Do NOT fallback to other tokens (like Graph API),
            // as they lack the correct permissions and often lack oid/tid claims.
            if (o.target && o.target.indexOf('ChatAI') !== -1) return o.secret;
          }
        } catch (e) {}
      }
    }
    return null;
  } catch (e) {}
  return null;
}
"""

# Discover the Graph API MSAL access token from localStorage or sessionStorage.
# This token is required for uploading documents (PDF, DOCX, etc.) to OneDrive.
_FIND_GRAPH_TOKEN_JS = """
() => {
  try {
    const stores = [localStorage, sessionStorage];
    for (const store of stores) {
      if (!store) continue;
      for (let i = 0; i < store.length; i++) {
        const k = store.key(i);
        const v = store.getItem(k);
        if (v && v.indexOf('"credentialType":"AccessToken"') !== -1) {
          try {
            const o = JSON.parse(v);
            if (o && o.secret) {
              if (o.target && (o.target.indexOf('graph.microsoft.com') !== -1 || o.target.indexOf('Files.') !== -1)) {
                 return o.secret;
              }
            }
          } catch (e) {}
        }
      }
    }
    return null;
  } catch (e) {}
  return null;
}
"""
# True once the user is signed in, *before* the chat token is minted. MSAL writes
# an `msal.*.account.keys` index (a non-empty list of cached accounts) the moment
# sign-in completes — and, crucially, this index is NOT encrypted even when the
# token cache itself is, so it is a reliable sign-in signal for every account
# type (Microsoft *and* federated Google). We deliberately do not key off the
# ChatAI access token here: for Google logins MSAL stores the token cache
# *encrypted* ({id,nonce,data,...}) and only mints the chat token on the first
# chat turn, so waiting for it during login would never succeed (see login()).
_SIGNED_IN_JS = """
() => {
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (k && k.indexOf('account.keys') !== -1) {
        try {
          const a = JSON.parse(localStorage.getItem(k) || 'null');
          if (Array.isArray(a) ? a.length > 0 : (a && Object.keys(a).length > 0))
            return true;
        } catch (e) {}
      }
    }
  } catch (e) {}
  return false;
}
"""


class BrowserCopilot:
    """Drives Microsoft Copilot through a real Playwright browser.

    Parameters
    ----------
    profile_dir:
        Directory for the persistent Chromium profile (cookies, sign-in).
        Reused across runs.
    headless:
        Run without a visible window. Use ``False`` (or :meth:`login`) for the
        first interactive sign-in, then ``True`` afterwards.
    """

    label = "Microsoft Copilot (browser)"
    default_model = "Copilot"

    def __init__(
        self,
        profile_dir: str = DEFAULT_PROFILE_DIR,
        headless: bool = True,
        nav_timeout: int = 180,
        proxy: Optional[str] = None,
    ):
        self.profile_dir = str(Path(profile_dir).resolve())
        self.headless = headless
        self.nav_timeout = nav_timeout
        # E5 Substrate chat can be geo-restricted in certain network environments.
        # If needed, route the browser through a proxy/VPN, e.g.
        # proxy="http://user:pass@host:port" or "socks5://host:port".
        self.proxy = proxy

        self._pw = None
        self._context = None
        self._page = None
        self._login_log_fh = None
        # Chat token captured live off the page's own chat WebSocket. This is the
        # only way to recover the token for sessions whose MSAL cache is encrypted
        # (e.g. federated Google logins), where _FIND_TOKEN_JS cannot read it.
        self._captured_chat_token: Optional[str] = None
        self._captured_identity_type: Optional[str] = None
        self._captured_refresh_token: Optional[str] = None
        self._ws_listener_installed = False
        self._network_listener_installed = False
        # Set True once the page's chat socket streams a reply (an ``appendText``
        # frame). This is the true success signal that the turn passed and
        # session cookies are valid.
        self._warmup_replied = False

    # -- lifecycle ----------------------------------------------------------

    def start(self, headless: Optional[bool] = None) -> "BrowserCopilot":
        """Launch the persistent browser context and open Copilot."""
        if self._context is not None:
            return self
        if headless is not None:
            self.headless = headless
        try:
            self._pw = sync_playwright().start()
            launch_kwargs: Dict[str, Any] = dict(
                headless=self.headless,
                locale=US_LOCALE,
                timezone_id=US_TIMEZONE,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    f"--lang={US_LOCALE}",
                    "--window-position=0,0",
                    "--window-size=1280,800",
                ],
                ignore_default_args=["--enable-automation"],
            )
            # Pin the UA on every launch to match standard browser client hints.
            launch_kwargs["user_agent"] = _STEALTH_UA
            if self.proxy:
                launch_kwargs["proxy"] = self._parse_proxy(self.proxy)
            self._context = self._pw.chromium.launch_persistent_context(
                self.profile_dir,
                **launch_kwargs,
            )
            try:
                self._context.set_extra_http_headers({"Accept-Language": US_ACCEPT_LANGUAGE})
            except PlaywrightError:
                pass
            # Mask the residual navigator.webdriver flag for every frame.
            try:
                self._context.add_init_script(_STEALTH_INIT_JS)
            except PlaywrightError:
                pass
            self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
            self._page.set_default_timeout(self.nav_timeout * 1000)
            self._page.goto(COPILOT_URL, wait_until="domcontentloaded")
            # Give the SPA a moment to settle on first paint. We deliberately do
            # NOT wait for "networkidle": Copilot's SPA keeps telemetry/heartbeat
            # connections open indefinitely, so the network never goes idle and the
            # wait would always time out. A short fixed settle is enough.
            self._page.wait_for_timeout(2000)
        except PlaywrightError as exc:
            if not self.headless:
                print(f"[Browser] VNC browser launch failed: {exc}. Keeping browser window open for 60s for troubleshooting...")
                try:
                    time.sleep(60)
                except Exception:
                    pass
            self.close()
            raise ConnectionError(f"Failed to start browser: {exc}") from exc
        return self

    @staticmethod
    def _parse_proxy(proxy: str) -> dict:
        """Turn a ``scheme://user:pass@host:port`` string into Playwright form."""
        from urllib.parse import urlparse

        u = urlparse(proxy)
        server = f"{u.scheme}://{u.hostname}:{u.port}" if u.port else f"{u.scheme}://{u.hostname}"
        cfg = {"server": server}
        if u.username:
            cfg["username"] = u.username
        if u.password:
            cfg["password"] = u.password
        return cfg

    def region_blocked(self) -> bool:
        """True if Copilot is showing the 'Not available in your region' notice."""
        if self._page is None:
            return False
        try:
            text = self._page.evaluate("() => document.body ? document.body.innerText : ''")
        except PlaywrightError:
            return False
        return "available in your region" in (text or "").lower()

    def close(self) -> None:
        if getattr(self, "_context", None):
            try:
                if getattr(self._context, "browser", None):
                    self._context.browser.close()
            except Exception:
                pass
        for attr, closer in (
            ("_context", lambda c: c.close()),
            ("_pw", lambda p: p.stop()),
            ("_login_log_fh", lambda f: f.close()),
        ):
            obj = getattr(self, attr, None)
            if obj is not None:
                try:
                    closer(obj)
                except Exception:
                    pass
                setattr(self, attr, None)
        self._page = None

        # Use Playwright official driver cleanup API (recommended)
        try:
            import playwright._impl._driver as driver
            if hasattr(driver, "cleanup"):
                driver.cleanup()
        except Exception:
            pass

    def __enter__(self) -> "BrowserCopilot":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.close()

    # -- auth ---------------------------------------------------------------

    def login(self, path: str = DEFAULT_AUTH_FILE, timeout: int = 300) -> dict:
        """Open a visible window for interactive Microsoft/Google sign-in.

        Auto-detects success — a cached account appearing in the page (the moment
        sign-in completes, see :data:`_SIGNED_IN_JS`) — then **warms up** the
        session with one throwaway chat turn to mint the Copilot chat token and
        captures it off the page's own chat WebSocket. This warm-up is what makes
        federated *Google* logins work: their MSAL cache is encrypted and the chat
        token is only minted on the first turn, so the old "wait for the token in
        localStorage" approach timed out (~5 min) and saved a null token.
        Microsoft accounts already have a readable token, so the warm-up returns
        instantly and their flow is unchanged.

        No key-press needed; the browser closes itself. Every step is appended to
        ``<session>/login.log``. ``timeout`` bounds the wait. The session persists
        in ``profile_dir`` for headless reuse.
        """
        self.close()
        self.start(headless=False)
        self._install_ws_listener()
        self._install_network_listener()

        log = self._open_login_log(Path(path).resolve().parent / "login.log")
        log(f"login started; browser open at {COPILOT_URL}")
        self._mirror_page_events(log)

        print(
            "\nA browser window is open at copilot.microsoft.com.\n"
            "Sign in (and pass any 'verify you're human' check).\n"
            "It finishes by itself once sign-in is detected — no need to press Enter.\n"
        )

        # Wait for sign-in (a cached account), not for the chat token: the token
        # may not exist until the first turn. Bail early on window close/timeout.
        detected = False
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._window_closed():
                log("browser window closed before sign-in was detected")
                break
            if self.signed_in():
                log("sign-in detected (account cached)")
                detected = True
                break
            try:
                self._page.wait_for_timeout(1500)
            except PlaywrightError:
                break

        token = None
        if detected:
            print("Signed in — finishing setup (warm-up)...")
            log("warming up to mint the chat token")
            try:
                self._ensure_on_chat()
                self._warmup_replied = False
                if self._send_warmup():
                    self._await_warmup_reply(timeout=max(30, int(deadline - time.time())))
                token = self.access_token()
            except PlaywrightError as exc:
                log(f"warm-up error: {exc}")
            log(f"chat token captured: {'yes' if token else 'no'}"
                f" (identity={self._captured_identity_type})")
        else:
            log(f"not signed in within {timeout}s; snapshotting current state")
            print("Sign-in not detected; saving whatever session state exists.")

        # Snapshot for the headless curl_cffi path.
        auth: dict = {}
        success = False
        try:
            auth = self.export_auth(path=path, stamp=time.time(), login_stamp=time.time())
            log(f"auth snapshot saved to {path} (access_token={'yes' if auth.get('access_token') else 'no'}"
                f", identity={auth.get('identity_type')})")
            print(f"Auth snapshot saved to {path}")
            success = True
        except Exception as exc:
            log(f"could not snapshot auth: {exc}")
            print(f"(could not snapshot auth: {exc})")

        log("closing browser")
        if not success and not self.headless:
            print("[Browser] Login failed or token capture failed. Keeping browser open for 60s for troubleshooting in VNC...")
            try:
                time.sleep(60)
            except Exception:
                pass
        self.close()
        print(f"Session saved to {self.profile_dir}")
        return auth

    def _open_login_log(self, log_path: Path):
        """Return a best-effort timestamped append-logger to ``log_path``.

        The handle is parked on the context so :meth:`close` can release it; if the
        file can't be opened, the returned logger is a silent no-op.
        """
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            self._login_log_fh = log_path.open("a", encoding="utf-8")
        except OSError:
            self._login_log_fh = None

        def log(message: str) -> None:
            fh = self._login_log_fh
            if fh is None:
                return
            try:
                fh.write(f"{datetime.now(timezone.utc).isoformat()}\t{message}\n")
                fh.flush()
            except Exception:
                pass

        return log

    def _mirror_page_events(self, log) -> None:
        """Stream main-frame navigations and console errors into the login log."""
        try:
            self._page.on(
                "framenavigated",
                lambda fr: fr == self._page.main_frame and log(f"navigated: {fr.url}"),
            )
            self._page.on(
                "console",
                lambda m: m.type == "error" and log(f"console.error: {m.text}"),
            )
        except PlaywrightError:
            pass

    def _window_closed(self) -> bool:
        """True if the page/context is gone (e.g. the user closed the window)."""
        try:
            return self._page is None or self._page.is_closed()
        except Exception:
            return True

    def access_token(self) -> Optional[str]:
        """Return the Copilot chat token, or ``None`` if not available.

        Prefers a token captured live off the page's own chat WebSocket (the only
        source that works when the MSAL cache is encrypted, e.g. Google logins),
        and otherwise falls back to reading the unencrypted MSAL cache via
        ``_FIND_TOKEN_JS`` (Microsoft logins). Call :meth:`acquire_chat_token`
        first to ensure one of these is populated.
        """
        if self._captured_chat_token:
            return self._captured_chat_token
        self._ensure_started()
        try:
            return self._page.evaluate(_FIND_TOKEN_JS)
        except PlaywrightError:
            return None

    def graph_token(self) -> Optional[str]:
        """Return the Graph API token required for document uploads, or ``None``."""
        self._ensure_started()
        try:
            return self._page.evaluate(_FIND_GRAPH_TOKEN_JS)
        except PlaywrightError:
            return None

    def signed_in(self) -> bool:
        """True once a Microsoft/Google account is cached (sign-in complete)."""
        self._ensure_started()
        try:
            return bool(self._page.evaluate(_SIGNED_IN_JS))
        except PlaywrightError:
            return False

    def _install_ws_listener(self) -> None:
        """Capture the chat token off the page's own chat WebSocket.

        The page opens ``wss://.../c/api/chat?...&accessToken=<token>`` (plus, for
        federated logins, ``&X-UserIdentityType=google``) when it sends a turn.
        Reading the token here is encryption-proof: the page has already decrypted
        it. parse_qs URL-decodes the value, so we store the raw token (the drivers
        re-quote it when building their own socket URL)."""
        if self._ws_listener_installed or self._page is None:
            return

        def on_ws(ws):
            try:
                url = ws.url
                if "/c/api/chat" not in url and "/Chathub" not in url and "/chathub" not in url:
                    return
                if "accessToken=" in url or "access_token=" in url:
                    q = parse_qs(urlparse(url).query)
                    tok = (q.get("accessToken") or q.get("access_token") or [None])[0]
                    if tok:
                        self._captured_chat_token = tok
                        self._captured_identity_type = (q.get("X-UserIdentityType") or [None])[0]
                # Watch reply frames so auto_clear knows the turn passed the gate.
                ws.on("framereceived", self._on_chat_frame)
            except Exception:
                pass

        try:
            self._page.on("websocket", on_ws)
            self._ws_listener_installed = True
        except PlaywrightError:
            pass

    def _install_network_listener(self) -> None:
        """Capture the refresh token from network responses during login redirects."""
        if getattr(self, "_network_listener_installed", False) or self._page is None:
            return

        def on_response(response):
            try:
                if "login.microsoftonline.com" in response.url and "/oauth2/v2.0/token" in response.url:
                    body = response.json()
                    if "refresh_token" in body:
                        self._captured_refresh_token = body["refresh_token"]
            except Exception:
                pass

        try:
            self._page.on("response", on_response)
            self._network_listener_installed = True
        except PlaywrightError:
            pass

    def _on_chat_frame(self, payload) -> None:
        """Flag a passed turn when the chat socket streams reply content.

        An ``appendText`` (or ``imageGenerated``) frame means the warm-up reply is
        flowing."""
        try:
            data = payload if isinstance(payload, str) else bytes(payload).decode("utf-8", "ignore")
        except Exception:
            return
        if "appendText" in data or "writeAtCursor" in data or "imageGenerated" in data or '"messages":' in data:
            self._warmup_replied = True

    def _send_warmup(self, text: str = "hi") -> bool:
        """Send one message through the page composer to mint the chat token.

        Returns True if a send was attempted. Federated (Google) sessions only
        mint the ChatAI token on the first chat turn, so we trigger one here and
        let :meth:`_install_ws_listener` capture the token off the resulting
        socket."""
        for sel in ("textarea", "div[contenteditable='true']", "[role='textbox']"):
            try:
                self._page.wait_for_selector(sel, state="visible", timeout=8000)
            except PlaywrightError:
                continue
            try:
                self._page.click(sel)
                self._page.keyboard.type(text, delay=15)
                self._page.keyboard.press("Enter")
                return True
            except PlaywrightError:
                continue
        return False

    def acquire_chat_token(
        self, timeout: int = 180, warmup: bool = True, signin_grace: int = 30
    ) -> Optional[str]:
        """Return a usable chat token, minting it via a warm-up turn if needed.

        Fast path: a token already readable (captured, or unencrypted MSAL cache)
        is returned immediately — this is the common Microsoft case. Otherwise, if
        ``warmup`` and the user is signed in, send one throwaway message and
        capture the token off the chat WebSocket (the encrypted-cache / Google
        case). Returns ``None`` if no token could be obtained within ``timeout``.

        ``signin_grace`` bounds how long we wait for an *existing* sign-in to
        register before giving up. A headless refresh can't perform interactive
        sign-in, so on a not-signed-in profile we bail after this short grace
        instead of blocking the full ``timeout`` — that wait is what made the
        no-session path feel hung before it fell through to a visible login.
        Sign-in normally registers within ~1-2s of page load (an already-signed-in
        profile passes the grace immediately).
        """
        self._ensure_started()
        self._install_ws_listener()
        self._install_network_listener()

        tok = self.access_token()
        if tok or not warmup:
            return tok

        deadline = time.time() + timeout
        signin_deadline = time.time() + min(signin_grace, timeout)
        while time.time() < signin_deadline and not self.signed_in():
            if self._window_closed():
                return None
            self._page.wait_for_timeout(500)
        if not self.signed_in():
            return None

        self._ensure_on_chat()
        if not self._send_warmup():
            return self.access_token()

        while time.time() < deadline:
            if self._captured_chat_token:
                return self._captured_chat_token
            if self._window_closed():
                break
            self._page.wait_for_timeout(500)
        return self.access_token()

    def _await_warmup_reply(self, timeout: int = 60) -> bool:
        """Wait for an already-sent warm-up turn to receive a reply."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._window_closed():
                break
            if self._warmup_replied:
                break
            self._page.wait_for_timeout(500)
        return self._warmup_replied

    def cookies(self) -> Dict[str, str]:
        """Return the signed-in Microsoft cookies as a name->value dict."""
        self._ensure_started()
        try:
            raw = self._context.cookies()
        except PlaywrightError:
            return {}
        return {
            c["name"]: c["value"] 
            for c in raw 
            if any(domain in c.get("domain", "") for domain in ("microsoft.com", "microsoftonline.com", "office.com", "office365.com", "live.com", "bing.com"))
        }

    def _ensure_on_chat(self) -> None:
        """Force navigation to Copilot chat page if currently on a different page."""
        if self._page:
            try:
                current_url = self._page.url
                if "/chat" not in current_url:
                    print(f"[Browser] Redirecting from {current_url} to {COPILOT_URL}...")
                    self._page.goto(COPILOT_URL, wait_until="domcontentloaded")
                    self._page.wait_for_timeout(2000)
            except Exception:
                pass

    def export_auth(self, path: str = DEFAULT_AUTH_FILE, stamp: Optional[float] = None, login_stamp: Optional[float] = None) -> dict:
        """Save the chat token, graph token, and Microsoft cookies to ``path``.

        Returns the snapshot dict."""
        self._ensure_started()
        token = self.access_token()
        if not token:
            raise RuntimeError("Failed to capture a valid Microsoft Copilot access token.")

        # Try to read existing login_at from path
        existing_login_at = None
        dest = Path(path)
        if dest.exists():
            try:
                existing_data = json.loads(dest.read_text(encoding="utf-8"))
                existing_login_at = existing_data.get("login_at")
            except Exception:
                pass

        login_at = login_stamp or existing_login_at or stamp or time.time()

        auth = {
            "cookies": self.cookies(),
            "access_token": token,
            "graph_token": self.graph_token(),
            "refresh_token": getattr(self, "_captured_refresh_token", None),
            "identity_type": getattr(self, "_captured_identity_type", None),
            "saved_at": stamp or time.time(),
            "login_at": login_at,
        }
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(auth, indent=2), encoding="utf-8")
        return auth

    # -- internals ----------------------------------------------------------

    def _ensure_started(self) -> None:
        if self._context is None or self._page is None:
            self.start()
