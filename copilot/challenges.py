"""Copilot chat-socket proof-of-work challenge solvers.

Before streaming, the chat WebSocket sends a challenge that the client must
answer. These mirror copilot.microsoft.com's own client (hashcash.worker + entry
bundle): a ``hashcash`` proof-of-work and an arithmetic ``copilot`` variant.
"""

import hashlib
import math


def _hashcash_ok(digest: bytes, difficulty: int) -> bool:
    """True if ``digest`` has at least ``difficulty`` leading zero bits."""
    full, rem = difficulty // 8, difficulty % 8
    for i in range(full):
        if digest[i] != 0:
            return False
    if rem:
        mask = (255 << (8 - rem)) & 0xFF
        if digest[full] & mask != 0:
            return False
    return True


def solve_hashcash(parameter: str) -> str:
    """Solve a ``"<seed>:<difficulty>"`` hashcash challenge.

    Find the smallest nonce ``n`` such that ``sha256(seed + str(n))`` has
    ``difficulty`` leading zero bits. Returns the nonce as a string (the value
    the client sends back as ``token``).
    """
    seed, diff = parameter.rsplit(":", 1)
    difficulty = int(diff)
    n = 0
    while True:
        if _hashcash_ok(hashlib.sha256((seed + str(n)).encode()).digest(), difficulty):
            return str(n)
        n += 1


def solve_copilot_challenge(parameter: str) -> str:
    """Solve the arithmetic ``copilot`` challenge: round((a^3/100 + a*25) % 22)."""
    a = float(parameter)
    return str(int(math.floor(((a ** 3 / 100 + a * 25) % 22) + 0.5)))
