"""High-level Copilot client — the recommended entry point.

One client, many conversations addressed by id. :meth:`CopilotClient.chat`
returns the full reply plus the conversation id; pass that id back to continue
the same conversation, or omit it to start a fresh one. :meth:`CopilotClient.stream`
is the incremental variant.

    from copilot import CopilotClient

    client = CopilotClient()                       # loads signed-in auth once
    r = client.chat("My name is Tomato. Remember it.")
    print(r.text, r.conversation_id)

    r2 = client.chat("What's my name?", r.conversation_id)   # continue
    print(r2.text)

    for chunk in client.stream("Tell me a joke"):  # new conversation, streamed
        print(chunk, end="", flush=True)

The signed-in access token is refreshed transparently; sign in once with
``python -m copilot login``. Pass ``anonymous=True`` to skip sign-in (only where
anonymous consumer chat is available), or ``proxy=...`` to route through a
supported region.
"""

import sys
import time
from dataclasses import dataclass, field
from typing import Generator, List, Optional, Union

from .auth import AUTH_MAX_AGE, load_auth
from .driver import Copilot
from .models import Conversation, ImageResponse


def _status(msg: str) -> None:
    """Emit a recovery/progress line to stderr.

    Goes to stderr (not stdout) so it never mixes into the reply text the CLI and
    callers read off stdout. Plain ``print`` keeps it visible by default — this is
    a personal bridge, not a library that should stay silent."""
    print(f"[copilot] {msg}", file=sys.stderr, flush=True)


@dataclass
class ChatReply:
    """The full result of a :meth:`CopilotClient.chat` call."""

    text: str
    conversation_id: Optional[str]
    images: List[ImageResponse] = field(default_factory=list)


class ChatStream:
    """Iterable stream of reply chunks that also exposes the conversation id.

    Yields ``str`` text chunks (and :class:`~copilot.models.ImageResponse` for
    generated images). ``conversation_id`` is known up front when continuing an
    existing conversation, and is populated as soon as iteration begins when a
    new conversation is created.
    """

    def __init__(self, chunks: Generator, conversation_id: Optional[str]):
        self._chunks = chunks
        self.conversation_id = conversation_id

    def __iter__(self) -> Generator[Union[str, ImageResponse], None, None]:
        for item in self._chunks:
            if isinstance(item, Conversation):
                self.conversation_id = item.conversation_id
            else:
                yield item


class CopilotClient:
    """A Copilot client: one object, many conversations addressed by id.

    Parameters
    ----------
    anonymous:
        Skip sign-in and talk to Copilot anonymously.
    proxy:
        Optional ``scheme://user:pass@host:port`` proxy, applied to both the
        auth refresh and every request.
    max_age:
        Seconds a cached access token is trusted before it is refreshed.
    """

    def __init__(
        self,
        anonymous: bool = False,
        proxy: Optional[str] = None,
        max_age: int = AUTH_MAX_AGE,
        session_dir: str = "session",
    ):
        self._driver = Copilot()
        self._anonymous = anonymous
        self._proxy = proxy
        self._max_age = max_age
        self._session_dir = session_dir
        self._auth: Optional[dict] = None

    def stream(
        self,
        prompt: str,
        conversation_id: Optional[str] = None,
        model: Optional[str] = None,
        **kwargs,
    ) -> ChatStream:
        """Stream the reply to ``prompt`` as a :class:`ChatStream`.

        Starts a new conversation when ``conversation_id`` is ``None``; otherwise
        continues that conversation. Read ``.conversation_id`` on the returned
        stream (during/after iteration) to continue the chat later.
        """
        return ChatStream(
            self._stream_direct(prompt, conversation_id, model, kwargs),
            conversation_id,
        )

    def _stream_direct(self, prompt, conversation_id, model, kwargs):
        """Drive the turn directly against E5 / Enterprise Copilot."""
        auth = self._fresh_auth()
        kw = dict(
            stream=True,
            proxy=self._proxy,
            cookies=auth["cookies"] if auth else None,
            access_token=auth["access_token"] if auth else None,
            identity_type=auth.get("identity_type") if auth else None,
            model=model,
            **kwargs,
        )
        if conversation_id is None:
            kw["return_conversation"] = True  # have the driver hand back its id
        else:
            kw["conversation_id"] = conversation_id

        for item in self._driver.create_completion(prompt, **kw):
            yield item

    def chat(
        self,
        prompt: str,
        conversation_id: Optional[str] = None,
        model: Optional[str] = None,
        **kwargs,
    ) -> ChatReply:
        """Return the full reply to ``prompt`` as a :class:`ChatReply`.

        Buffers the whole response; use :meth:`stream` for incremental output.
        """
        s = self.stream(prompt, conversation_id=conversation_id, model=model, **kwargs)
        text: List[str] = []
        images: List[ImageResponse] = []
        for item in s:
            if isinstance(item, str):
                text.append(item)
            elif isinstance(item, ImageResponse):
                images.append(item)
        return ChatReply("".join(text), s.conversation_id, images)

    def _fresh_auth(self) -> Optional[dict]:
        """Return current signed-in auth, refreshing it when stale (or None)."""
        if self._anonymous:
            return None
        if self._auth is None or (time.time() - self._auth.get("saved_at", 0)) >= self._max_age:
            self._auth = load_auth(
                path=f"{self._session_dir}/token.json",
                profile_dir=f"{self._session_dir}/profile",
                max_age=self._max_age,
                proxy=self._proxy
            )
        return self._auth
