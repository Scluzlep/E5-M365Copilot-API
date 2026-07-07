"""Context Router for seamless multi-turn conversation reuse."""

import hashlib
import threading
from typing import Dict, Tuple, Optional
from server.accounts import pool
from server.prompt import content_text, messages_to_prompt

class ConversationState:
    def __init__(self, conversation_id: str, head_hash: str, session_name: str):
        self.conversation_id = conversation_id
        self.head_hash = head_hash
        self.session_name = session_name

class ConversationRouter:
    def __init__(self):
        import collections
        import os
        # Maps head_hash -> ConversationState (used as an LRU cache)
        self.states: collections.OrderedDict[str, ConversationState] = collections.OrderedDict()
        # Maps conversation_id -> current_head_hash
        self.active_heads: Dict[str, str] = {}
        self._lock = threading.Lock()
        self.persist_path = os.path.join("sessions", "conversations.json")
        self._dirty = False
        self._timer: Optional[threading.Timer] = None
        self._debounce_seconds = 2.0
        self._load_from_disk()

    def _load_from_disk(self):
        import json, os
        if os.path.exists(self.persist_path):
            try:
                with open(self.persist_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for h, item in data.get("states", {}).items():
                    self.states[h] = ConversationState(item["conversation_id"], h, item.get("session_name", ""))
                self.active_heads = data.get("active_heads", {})
            except Exception as e:
                import sys
                print(f"[router] Failed to load persisted state: {e}", file=sys.stderr)

    def _schedule_save(self):
        # Must be called with self._lock held
        self._dirty = True
        if self._timer is None:
            self._timer = threading.Timer(self._debounce_seconds, self._flush_disk)
            self._timer.daemon = True
            self._timer.start()

    def _flush_disk(self):
        with self._lock:
            if not self._dirty:
                self._timer = None
                return
            self._dirty = False
            self._timer = None
            data = {
                "states": {h: {"conversation_id": s.conversation_id, "session_name": s.session_name} for h, s in self.states.items()},
                "active_heads": self.active_heads
            }
        
        import json, os
        try:
            os.makedirs("sessions", exist_ok=True)
            tmp_path = self.persist_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp_path, self.persist_path)
        except Exception as e:
            import sys
            print(f"[router] Failed to persist state: {e}", file=sys.stderr)

    def flush(self):
        with self._lock:
            if self._timer:
                self._timer.cancel()
                self._timer = None
        self._flush_disk()

    def _hash_messages(self, messages) -> str:
        """Create a deterministic hash from a list of ChatMessage."""
        import re
        s = ""
        for m in messages:
            content = content_text(m.content)
            content = re.sub(r'<think>.*?</think>\n*', '', content, flags=re.DOTALL)
            s += f"|{m.role}|{content}"
        return hashlib.sha256(s.encode("utf-8")).hexdigest()

    def route(self, api_key: str, messages, client_provided_cid: Optional[str]) -> Tuple[Optional[str], str, str, Optional[str]]:
        """
        Determine if we can reuse an existing conversation ID.
        
        Returns:
            conversation_id: str (or None if starting fresh)
            prompt: str (the text to send)
            new_head_hash: str (the hash of the entire messages array)
            session_name: str (the name of the session that owns the conversation)
        """
        if not pool.is_valid_key(api_key):
            raise ValueError(f"Invalid API Key: {api_key}")
            
        if not messages:
            return None, "", "", None
            
        # If client explicitly provides a conversation_id, trust it directly.
        if client_provided_cid:
            return client_provided_cid, messages_to_prompt(messages), self._hash_messages(messages), None
            
        history = messages[:-1]
        last_msg = messages[-1]
        
        raw_history_hash = self._hash_messages(history)
        raw_new_hash = self._hash_messages(messages)
        
        # Get all bound session names for this api_key to check compound keys
        session_names = []
        with pool._config_lock:
            session_names = list(pool.api_keys.get(api_key, []))
        
        # Check if this exact history is the current head of a tracked conversation
        with self._lock:
            for s_name in session_names:
                compound_key = f"{api_key}:{s_name}:{raw_history_hash}"
                if compound_key in self.states:
                    state = self.states[compound_key]
                    self.states.move_to_end(compound_key)
                    prompt = content_text(last_msg.content)
                    if last_msg.role != "user":
                        prompt = f"{last_msg.role.capitalize()}: {prompt}"
                    return state.conversation_id, prompt, raw_new_hash, state.session_name
            
            # Also check fallback (legacy non-compound hash or single session)
            if raw_history_hash in self.states:
                state = self.states[raw_history_hash]
                self.states.move_to_end(raw_history_hash)
                prompt = content_text(last_msg.content)
                if last_msg.role != "user":
                    prompt = f"{last_msg.role.capitalize()}: {prompt}"
                return state.conversation_id, prompt, raw_new_hash, state.session_name
            
        # Fallback: Flatten the entire history and start a new Copilot thread
        prompt = messages_to_prompt(messages)
        return None, prompt, raw_new_hash, None
        
    def save_state(self, new_head_hash: str, conversation_id: str, session_name: str, api_key: str = ""):
        """Record the new state after a successful turn."""
        if new_head_hash and conversation_id and session_name:
            with self._lock:
                old_head = self.active_heads.get(conversation_id)
                if old_head and old_head in self.states:
                    del self.states[old_head]
                
                compound_key = f"{api_key}:{session_name}:{new_head_hash}" if api_key else new_head_hash
                self.states[compound_key] = ConversationState(conversation_id, compound_key, session_name)
                self.active_heads[conversation_id] = compound_key
                
                # Evict oldest states if it grows too large (FIFO from OrderedDict)
                while len(self.states) > 10000:
                    oldest_key, old_state = self.states.popitem(last=False)
                    if self.active_heads.get(old_state.conversation_id) == oldest_key:
                        self.active_heads.pop(old_state.conversation_id, None)
                
                self._schedule_save()

# Global router instance
router = ConversationRouter()
