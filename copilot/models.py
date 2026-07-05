"""Shared data types for the Copilot drivers.

Plain containers and the provider interface — no protocol or I/O logic, so both
the pure-HTTP (:mod:`copilot.client`) and browser (:mod:`copilot.browser`) paths
can depend on these without pulling in each other's dependencies.
"""

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from http.cookiejar import CookieJar
from typing import Any, Dict, Generator, Union

ImageType = Union[str, bytes]


@dataclass
class ImageResponse:
    url: str
    prompt: str
    metadata: Dict[str, Any]


class AbstractProvider(ABC):
    label: str
    url: str
    working: bool
    supports_stream: bool
    default_model: str
    needs_auth: bool = True

    @abstractmethod
    def create_completion(self, *args, **kwargs) -> Generator:
        ...


class Conversation:
    def __init__(self, conversation_id: str, cookie_jar: CookieJar, access_token: str = None):
        self.conversation_id = conversation_id
        self.cookie_jar = cookie_jar
        self.access_token = access_token
        self._lock = threading.Lock()
        self._cookies_dict = {}
        self._update_cookies_dict()

    def _update_cookies_dict(self):
        self._cookies_dict = {cookie.name: cookie.value for cookie in self.cookie_jar}

    def update_token(self, new_token: str):
        with self._lock:
            self.access_token = new_token
            self._update_cookies_dict()

    @property
    def cookies(self):
        with self._lock:
            return self._cookies_dict.copy()
