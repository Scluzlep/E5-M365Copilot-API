"""Account pooling and API Key routing."""

import json
import os
import threading
from typing import Dict, List, Optional
from contextlib import contextmanager
import shutil

from copilot.client import CopilotClient
from .config import RATE_LIMIT_RPM, RATE_LIMIT_BURST
from .ratelimit import TokenBucket

class RateLimitExceeded(Exception):
    def __init__(self, wait_seconds: float):
        self.wait_seconds = wait_seconds

class SessionInstance:
    """Holds the runtime state for a single Copilot session (client and lock)."""
    def __init__(self, session_name: str):
        self.session_name = session_name
        self.session_dir = f"sessions/{session_name}"
        # We assume backward compatibility for 'session'
        if session_name == "session":
            self.session_dir = "session"
        self.client = CopilotClient(session_dir=self.session_dir)
        self.lock = threading.Lock()
        self.rate_limiter = TokenBucket(RATE_LIMIT_RPM, RATE_LIMIT_BURST)
        self._health_cache_time = 0
        self._health_cache_val = False

    def is_healthy(self) -> bool:
        """Check if the session token is present and structurally valid/unexpired. Caches for 10s."""
        import time
        now = time.time()
        if now - self._health_cache_time < 10:
            return self._health_cache_val
            
        token_file = os.path.join(self.session_dir, "token.json")
        if not os.path.exists(token_file):
            self._health_cache_val = False
            self._health_cache_time = now
            return False
        try:
            with open(token_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                token = data.get("access_token", "")
                if not token:
                    self._health_cache_val = False
                    self._health_cache_time = now
                    return False
                
                parts = token.split(".")
                if len(parts) >= 2:
                    import base64
                    padded = parts[1] + '=' * (-len(parts[1]) % 4)
                    payload = json.loads(base64.urlsafe_b64decode(padded).decode('utf-8'))
                    
                    import time
                    exp = payload.get("exp")
                    # Optionally check expiration if present (allow some buffer)
                    if exp and time.time() > (exp - 60):
                        self._health_cache_val = False
                        self._health_cache_time = now
                        return False
                    
                    if not payload.get("oid") or not payload.get("tid"):
                        self._health_cache_val = False
                        self._health_cache_time = now
                        return False
                        
                    self._health_cache_val = True
                    self._health_cache_time = now
                    return True
        except Exception:
            pass
        self._health_cache_val = False
        self._health_cache_time = now
        return False

    def get_info(self) -> dict:
        info = {"tid": "N/A", "oid": "N/A", "email": "N/A", "status": "未登录"}
        token_file = os.path.join(self.session_dir, "token.json")
        if not os.path.exists(token_file):
            return info
            
        info["status"] = "已配置"
        try:
            with open(token_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                
                # The most reliable way to get TID, OID, and Email is to decode the JWT access_token
                token = data.get("access_token", "")
                if token:
                    parts = token.split(".")
                    if len(parts) >= 2:
                        import base64
                        padded = parts[1] + '=' * (-len(parts[1]) % 4)
                        payload = json.loads(base64.urlsafe_b64decode(padded).decode('utf-8'))
                        info["tid"] = payload.get("tid", "N/A")
                        info["oid"] = payload.get("oid", "N/A")
                        info["email"] = payload.get("unique_name", payload.get("upn", payload.get("email", "N/A")))

                # Fallback to cookies if JWT is missing or invalid
                # Note: 'cookies' is a dict of name->value, not a list of dicts.
                cookies = data.get("cookies", {})
                if isinstance(cookies, dict):
                    for name, val in cookies.items():
                        if name == "TIDC" and info["tid"] == "N/A":
                            info["tid"] = val
                        elif name == "OIDC" and info["oid"] == "N/A":
                            info["oid"] = val
                        elif "@" in val and not " " in val and len(val) < 60 and info["email"] == "N/A":
                            import urllib.parse
                            decoded = urllib.parse.unquote(val)
                            if "@" in decoded and "." in decoded and "{" not in decoded:
                                info["email"] = decoded
        except Exception:
            pass
        return info

    def get_email(self) -> str:
        if hasattr(self, "_cached_email") and self._cached_email:
            return self._cached_email
        email = self.get_info().get("email", "unknown")
        if email != "N/A" and email != "unknown":
            self._cached_email = email
        return email

class AccountPool:
    """Manages multiple E5 Copilot accounts and routes API keys to them."""
    
    def __init__(self, config_path: str = "accounts.json"):
        self.config_path = config_path
        self.sessions: Dict[str, SessionInstance] = {}
        self.api_keys: Dict[str, List[str]] = {} # api_key -> list of session_names
        self._round_robin_counters: Dict[str, int] = {}
        self._config_lock = threading.Lock()
        self._session_released_cv = threading.Condition()
        self._load_config()

    def _load_config(self):
        """Load API keys mapping from accounts.json, or fallback to default."""
        with self._config_lock:
            if os.path.exists(self.config_path):
                try:
                    with open(self.config_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        
                        # Handle v2 format: {"api_keys": {"sk-test": ["session1"]}}
                        if "api_keys" in data:
                            self.api_keys = data["api_keys"]
                        else:
                            # Handle v1 format: {"sk-test": {"session_dir": "session"}}
                            self.api_keys = {}
                            for k, v in data.items():
                                sess_dir = v.get("session_dir", "session")
                                sess_name = sess_dir.replace("sessions/", "")
                                self.api_keys[k] = [sess_name]

                        # Instantiate all unique sessions
                        unique_sessions = set()
                        for sess_list in self.api_keys.values():
                            unique_sessions.update(sess_list)
                            
                        for sess_name in unique_sessions:
                            if sess_name not in self.sessions:
                                self.sessions[sess_name] = SessionInstance(sess_name)
                except Exception as e:
                    print(f"Failed to load {self.config_path}: {e}")
            
            # If config doesn't exist or is empty, we just start empty.
            if not self.api_keys:
                self._save_config_unlocked()

    def _save_config_unlocked(self):
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump({"api_keys": self.api_keys}, f, indent=4)
        except OSError:
            pass
            
    def get_api_keys(self) -> dict:
        with self._config_lock:
            return self.api_keys.copy()
            
    def get_sessions_info(self) -> dict:
        with self._config_lock:
            return {name: sess.get_info() for name, sess in self.sessions.items()}
            
    def add_api_key(self, api_key: str, sessions: List[str]):
        with self._config_lock:
            if api_key in self.api_keys:
                for s in sessions:
                    if s not in self.api_keys[api_key]:
                        self.api_keys[api_key].append(s)
            else:
                self.api_keys[api_key] = sessions
                
            for s in sessions:
                if s not in self.sessions:
                    self.sessions[s] = SessionInstance(s)
            self._save_config_unlocked()

    def _cleanup_orphaned_sessions(self):
        active = set()
        for sessions in self.api_keys.values():
            active.update(sessions)
            
        to_remove = []
        for sess_name in list(self.sessions.keys()):
            if sess_name not in active:
                to_remove.append(sess_name)
                
        for sess_name in to_remove:
            sess = self.sessions.pop(sess_name, None)
            if sess and os.path.exists(sess.session_dir):
                try:
                    shutil.rmtree(sess.session_dir)
                except Exception as e:
                    print(f"Warning: Failed to delete orphaned session {sess_name}: {e}")

    def remove_api_key(self, api_key: str):
        with self._config_lock:
            if api_key in self.api_keys:
                del self.api_keys[api_key]
                self._cleanup_orphaned_sessions()
                self._save_config_unlocked()

    def remove_session_from_key(self, api_key: str, session_name: str):
        with self._config_lock:
            if api_key in self.api_keys:
                if session_name in self.api_keys[api_key]:
                    self.api_keys[api_key].remove(session_name)
                    if not self.api_keys[api_key]:
                        del self.api_keys[api_key]
                    self._cleanup_orphaned_sessions()
                    self._save_config_unlocked()
            
    def add_session(self, session_name: str):
        with self._config_lock:
            if session_name not in self.sessions:
                self.sessions[session_name] = SessionInstance(session_name)

    def is_valid_key(self, api_key: str) -> bool:
        with self._config_lock:
            return api_key in self.api_keys

    @contextmanager
    def acquire_session(self, api_key: str, preferred_session: str = None):
        """
        Yields an available SessionInstance for the given API Key.
        Attempts non-blocking acquire across all bound sessions to maximize throughput.
        """
        acquired_session = None
        min_wait = float('inf')
        
        with self._config_lock:
            session_names = self.api_keys.get(api_key)
            if not session_names:
                raise ValueError(f"Invalid API Key: {api_key}")
                
            all_sessions = [self.sessions[s] for s in session_names if s in self.sessions]
            if not all_sessions:
                raise ValueError(f"API Key {api_key} has no valid bound sessions.")

            if api_key not in self._round_robin_counters:
                self._round_robin_counters[api_key] = 0
            # If no preferred session is provided, it's a new conversation, so advance the round-robin counter.
            if not preferred_session:
                self._round_robin_counters[api_key] = (self._round_robin_counters[api_key] + 1) % len(session_names)
            current_rr_idx = self._round_robin_counters[api_key]

        # Prioritize preferred_session, then healthy sessions, then round-robin order
        def sort_key(s):
            is_pref = (s.session_name == preferred_session)
            # Find the original index of this session to compute round-robin distance
            try:
                original_idx = session_names.index(s.session_name)
            except ValueError:
                original_idx = 0
            rr_distance = (original_idx - current_rr_idx) % len(session_names)
            return (not is_pref, not s.is_healthy(), rr_distance)
            
        all_sessions.sort(key=sort_key)
        
        def try_acquire(sess) -> bool:
            nonlocal min_wait
            if sess.lock.acquire(blocking=False):
                allowed, wait = sess.rate_limiter.try_acquire()
                if allowed:
                    if not sess.is_healthy():
                        try:
                            # Trigger Pure API token renewal (with browser fallback) while locked
                            sess.client._fresh_auth()
                        except Exception as e:
                            print(f"[Pool] Failed to renew session {sess.session_name}: {e}")
                            sess.lock.release()
                            return False
                    return True
                else:
                    sess.lock.release()
                    if wait < min_wait:
                        min_wait = wait
            return False

        # Fast path: Try non-blocking acquire on any bound session and check rate limit
        for sess in all_sessions:
            if try_acquire(sess):
                acquired_session = sess
                break
                
        # Slow path: Use Condition variable to wait for a session to free up
        while not acquired_session:
            any_busy = any(sess.lock.locked() for sess in all_sessions)
            
            if not any_busy:
                # All unlocked but we couldn't get one -> Rate limit is the blocker or renewal failed
                if min_wait < float('inf'):
                    raise RateLimitExceeded(min_wait)
                else:
                    raise RuntimeError(f"All bound sessions for API Key '{api_key}' failed authentication renewal or are unhealthy.")
                    
            with self._session_released_cv:
                # Wait for a session to be released, or timeout to re-check rate limits.
                # Cap the wait at 1.0s to remain responsive.
                wait_time = min(1.0, min_wait) if min_wait < float('inf') else 1.0
                self._session_released_cv.wait(timeout=wait_time)
                
            min_wait = float('inf')
            all_sessions.sort(key=sort_key)
            for sess in all_sessions:
                if try_acquire(sess):
                    acquired_session = sess
                    break
            
        try:
            yield acquired_session
        finally:
            acquired_session.lock.release()
            with self._session_released_cv:
                self._session_released_cv.notify_all()

# Global pool instance
pool = AccountPool()
