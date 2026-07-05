"""Account pooling and API Key routing."""

import json
import os
import threading
import random
from typing import Dict, List, Optional
from contextlib import contextmanager
from pydantic import BaseModel
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

    def get_info(self) -> dict:
        info = {"tid": "N/A", "oid": "N/A", "email": "N/A", "status": "未登录"}
        token_file = os.path.join(self.session_dir, "token.json")
        if not os.path.exists(token_file):
            return info
            
        info["status"] = "已配置"
        try:
            with open(token_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                for c in data.get("cookies", []):
                    name = c.get("name", "")
                    val = c.get("value", "")
                    if name == "TIDC":
                        info["tid"] = val
                    elif name == "OIDC":
                        info["oid"] = val
                    elif "@" in val and not " " in val and len(val) < 60:
                        # Simple heuristic for Microsoft account email in cookies
                        import urllib.parse
                        decoded = urllib.parse.unquote(val)
                        if "@" in decoded and "." in decoded and "{" not in decoded:
                            info["email"] = decoded
        except Exception:
            pass
        return info

class AccountPool:
    """Manages multiple E5 Copilot accounts and routes API keys to them."""
    
    def __init__(self, config_path: str = "accounts.json"):
        self.config_path = config_path
        self.sessions: Dict[str, SessionInstance] = {}
        self.api_keys: Dict[str, List[str]] = {} # api_key -> list of session_names
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
            
            # Fallback to default if empty (backward compatibility)
            if not self.api_keys:
                self.api_keys["sk-default"] = ["session"]
                self.sessions["session"] = SessionInstance("session")
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
    def acquire_session(self, api_key: str):
        """
        Yields an available SessionInstance for the given API Key.
        Attempts non-blocking acquire across all bound sessions to maximize throughput.
        """
        session_names = self.api_keys.get(api_key)
        if not session_names:
            raise ValueError(f"Invalid API Key: {api_key}")
            
        bound_sessions = [self.sessions[s] for s in session_names if s in self.sessions]
        if not bound_sessions:
            raise ValueError(f"API Key {api_key} has no valid bound sessions.")

        acquired_session = None
        min_wait = float('inf')

        # Fast path: Try non-blocking acquire on any bound session and check rate limit
        for sess in bound_sessions:
            if sess.lock.acquire(blocking=False):
                allowed, wait = sess.rate_limiter.try_acquire()
                if allowed:
                    acquired_session = sess
                    break
                else:
                    sess.lock.release()
                    if wait < min_wait:
                        min_wait = wait
                
        # Slow path: Use Condition variable to wait for a session to free up
        while not acquired_session:
            any_busy = any(sess.lock.locked() for sess in bound_sessions)
            
            if not any_busy:
                # All unlocked but we couldn't get one -> Rate limit is the blocker
                if min_wait < float('inf'):
                    raise RateLimitExceeded(min_wait)
                    
            with self._session_released_cv:
                # Wait for a session to be released, or timeout to re-check rate limits.
                # Cap the wait at 1.0s to remain responsive.
                wait_time = min(1.0, min_wait) if min_wait < float('inf') else 1.0
                self._session_released_cv.wait(timeout=wait_time)
                
            min_wait = float('inf')
            for sess in bound_sessions:
                if sess.lock.acquire(blocking=False):
                    allowed, wait = sess.rate_limiter.try_acquire()
                    if allowed:
                        acquired_session = sess
                        break
                    else:
                        sess.lock.release()
                        if wait < min_wait:
                            min_wait = wait
            
        try:
            yield acquired_session
        finally:
            acquired_session.lock.release()
            with self._session_released_cv:
                self._session_released_cv.notify_all()

# Global pool instance
pool = AccountPool()
