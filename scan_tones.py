"""Probe which M365 conversation tones (models) this account can actually use.

Sends a minimal throwaway turn per candidate tone to verify availability on your specific tenant.

Usage:
    python scan_tones.py                       # Probes active session in sessions/
    python scan_tones.py --session session1   # Probes specific session
    python scan_tones.py --all                # Probe all candidate and extended tones
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

from copilot.agent_registry import BASE_TONE_DEFINITIONS
from copilot.client import CopilotClient

CANDIDATE_TONES = [item["tone"] for item in BASE_TONE_DEFINITIONS] + [
    "Balanced", "Creative", "Precise", "Gpt_6_Astra", "Grok_4_5"
]


def probe_tone(client: CopilotClient, tone: str) -> tuple[str, str]:
    """Test a single tone against CopilotClient. Returns (verdict, detail)."""
    try:
        reply = client.chat("Reply with the single word: ok", model=tone)
        txt = (reply.text or "").strip()
        if not txt:
            return "unknown", "Empty response"
        if any(w in txt.lower() for w in ["sorry", "wasn't able to respond", "not available"]):
            return "refused", txt[:60]
        return "ok", txt[:60].replace("\n", " ")
    except Exception as e:
        err = str(e)
        if "Failed" in err or "InternalError" in err:
            return "refused", err[:60]
        return "error", err[:60]


def main():
    parser = argparse.ArgumentParser(description="Probe available M365 Copilot tones.")
    parser.add_argument("--session", default="session", help="Session directory name (default: session)")
    parser.add_argument("--all", action="store_true", help="Probe all candidate tones")
    parser.add_argument("--delay", type=float, default=2.0, help="Delay in seconds between requests")
    args = parser.parse_args()

    session_dir = f"sessions/{args.session}" if not os.path.exists(args.session) else args.session
    print(f"[ScanTones] Initializing client for session: {session_dir}")
    client = CopilotClient(session_dir=session_dir)

    tones_to_probe = CANDIDATE_TONES if args.all else [item["tone"] for item in BASE_TONE_DEFINITIONS]
    # Deduplicate while preserving order
    seen = set()
    tones = []
    for t in tones_to_probe:
        if t not in seen:
            seen.add(t)
            tones.append(t)

    print(f"[ScanTones] Probing {len(tones)} tones against your tenant...")
    print("-" * 65)
    print(f"{'Tone':<26} {'Verdict':<10} {'Detail'}")
    print("-" * 65)

    results = []
    for tone in tones:
        verdict, detail = probe_tone(client, tone)
        status_icon = "✅" if verdict == "ok" else ("⛔" if verdict == "refused" else "❓")
        print(f"{status_icon} {tone:<24} {verdict:<10} {detail}")
        results.append((tone, verdict))
        time.sleep(args.delay)

    print("-" * 65)
    ok_tones = [t for t, v in results if v == "ok"]
    print(f"[Summary] {len(ok_tones)}/{len(tones)} tones returned valid responses.")


if __name__ == "__main__":
    main()
