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
        # Maps head_hash -> ConversationState
        self.states: Dict[str, ConversationState] = {}
        # Maps conversation_id -> current_head_hash
        self.active_heads: Dict[str, str] = {}
        self._lock = threading.Lock()

    def _hash_messages(self, messages) -> str:
        """Create a deterministic hash from a list of ChatMessage."""
        import re
        s = ""
        for m in messages:
            content = content_text(m.content)
            content = re.sub(r'<think>.*?</think>\n*', '', content, flags=re.DOTALL)
            s += f"|{m.role}|{content}"
        return hashlib.md5(s.encode("utf-8")).hexdigest()

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
        
        history_hash = self._hash_messages(history)
        new_head_hash = self._hash_messages(messages)
        
        # Check if this exact history is the current head of a tracked conversation
        with self._lock:
            if history_hash in self.states:
                state = self.states[history_hash]
                prompt = content_text(last_msg.content)
                # Add a cue if it's from a user, though Copilot usually figures it out.
                if last_msg.role != "user":
                    prompt = f"{last_msg.role.capitalize()}: {prompt}"
                return state.conversation_id, prompt, new_head_hash, state.session_name
            
        # Fallback: Flatten the entire history and start a new Copilot thread
        prompt = messages_to_prompt(messages)
        return None, prompt, new_head_hash, None
        
    def save_state(self, new_head_hash: str, conversation_id: str, session_name: str):
        """Record the new state after a successful turn."""
        if new_head_hash and conversation_id and session_name:
            with self._lock:
                old_head = self.active_heads.get(conversation_id)
                if old_head and old_head in self.states:
                    del self.states[old_head]
                    
                self.states[new_head_hash] = ConversationState(conversation_id, new_head_hash, session_name)
                self.active_heads[conversation_id] = new_head_hash
                
                # Evict oldest states if it grows too large
                while len(self.states) > 10000:
                    oldest_key = next(iter(self.states))
                    old_state = self.states.pop(oldest_key)
                    self.active_heads.pop(old_state.conversation_id, None)

# Global router instance
router = ConversationRouter()
