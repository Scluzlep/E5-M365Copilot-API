"""Rate-limit probe — fire requests at a true wall-clock rate and see what sticks.

The stress test (tests/stress.py) probes **concurrency**: how many requests can
be in flight at once. This probes **rate**: how many requests per minute actually
get through. It distinguishes two kinds of rejection:

  * HTTP 429  — the *bridge's own* limiter (RATE_LIMIT_RPM) throttling you. Expected
                whenever --rpm exceeds the configured limit; proof it works.
  * HTTP 502  — *Copilot* rejecting upstream (intermittent on datacenter IPs).

This sender is **open-loop**: it fires each request on a fixed timer in its own
thread and does NOT wait for the reply before sending the next. That's the whole
point — a *sequential* sender is capped by round-trip latency (~3.5s/call ≈ 17
req/min) and can never actually produce 30 req/min, so it would never stress a
limiter set above ~17. Open-loop means --rpm 30 really sends 30 requests/minute.

    # Test the bridge's limiter: set it low, then send above it
    RATE_LIMIT_RPM=15 python app.py
    python tests/ratelimit.py --rpm 30 --minutes 2     # expect ~half 429'd

    # Probe Copilot's own ceiling: turn the bridge limiter off first
    RATE_LIMIT_RPM=0 python app.py
    python tests/ratelimit.py --rpm 20 --minutes 3

Be considerate: this sends real traffic to your Copilot account. Keep it modest.
"""

import argparse
import json
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

PROMPT = "Reply with a single word: ok"


def classify(url, timeout):
    """Send one chat completion. Returns (category, elapsed, detail).

    category is one of: 'ok', '429', '502', 'other' — so the summary can tell the
    bridge's own throttling (429) apart from upstream Copilot failures (502).
    """
    body = json.dumps({
        "model": "copilot",
        "messages": [{"role": "user", "content": PROMPT}],
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        elapsed = time.perf_counter() - start
        if "error" in payload:
            return "other", elapsed, f"error payload: {payload['error']}"
        content = payload["choices"][0]["message"]["content"]
        return "ok", elapsed, content.strip()[:40]
    except urllib.error.HTTPError as exc:
        elapsed = time.perf_counter() - start
        cat = "429" if exc.code == 429 else "502" if exc.code == 502 else "other"
        return cat, elapsed, f"HTTP {exc.code}: {exc.reason}"
    except Exception as exc:
        return "other", time.perf_counter() - start, f"{type(exc).__name__}: {exc}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8000",
                        help="Server base URL (default: http://localhost:8000)")
    parser.add_argument("--rpm", type=float, default=20,
                        help="Requests per minute to fire (default: 20)")
    parser.add_argument("--minutes", type=float, default=3,
                        help="How long to sustain the load (default: 3)")
    parser.add_argument("--timeout", type=float, default=180,
                        help="Per-request timeout in seconds (default: 180)")
    args = parser.parse_args()

    endpoint = args.url.rstrip("/") + "/v1/chat/completions"
    if args.rpm <= 0:
        parser.error("--rpm must be > 0")
    interval = 60.0 / args.rpm
    total = max(1, round(args.rpm * args.minutes))

    print(f"Probing {endpoint}")
    print(f"Firing {args.rpm:g} req/min (1 every {interval:.1f}s) "
          f"open-loop for {args.minutes:g} min  →  {total} requests\n")

    results = {}          # seq -> (category, elapsed, detail)
    lock = threading.Lock()
    # Workers must outnumber peak in-flight requests, or sends would queue and
    # drift off-schedule. Latency-bound calls (~4s) at this rate set the ceiling.
    workers = max(8, int(args.rpm * 4 / 60) + 4)

    def fire(seq):
        cat, elapsed, detail = classify(endpoint, args.timeout)
        with lock:
            results[seq] = (cat, elapsed, detail)
        mark = {"ok": "✓", "429": "⊘", "502": "✗", "other": "✗"}[cat]
        print(f"  [{seq:>3}] {mark} {elapsed:5.1f}s  {detail}")

    start = time.perf_counter()
    sent = 0
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for i in range(total):
                # Sleep until this request's scheduled wall-clock slot, then fire
                # WITHOUT waiting for the response — that keeps the send rate true.
                target = start + i * interval
                drift = target - time.perf_counter()
                if drift > 0:
                    time.sleep(drift)
                pool.submit(fire, i + 1)
                sent += 1
            print("\n  …all requests sent; waiting for in-flight replies…")
    except KeyboardInterrupt:
        print("\nInterrupted — waiting for in-flight replies…")

    elapsed_total = time.perf_counter() - start
    done = results  # ThreadPoolExecutor.__exit__ has joined all workers
    cats = {"ok": 0, "429": 0, "502": 0, "other": 0}
    for cat, _, _ in done.values():
        cats[cat] += 1
    completed = len(done)
    achieved = sent / elapsed_total * 60 if elapsed_total else 0

    print(f"\nDuration {elapsed_total:.0f}s  ·  sent {sent}  ·  "
          f"achieved send rate {achieved:.1f} req/min (target {args.rpm:g})")
    print(f"Completed {completed}:  ok {cats['ok']}  ·  "
          f"429 bridge-limited {cats['429']}  ·  502 upstream {cats['502']}  ·  "
          f"other {cats['other']}")

    if cats["429"]:
        print("→ The bridge's own limiter (RATE_LIMIT_RPM) is throttling you, as "
              "designed. Lower --rpm or raise RATE_LIMIT_RPM to send more.")
    if cats["502"]:
        print("→ 502s are Copilot rejecting upstream (independent of rate). "
              "Retry-on-502 after a delay or maybe use exponential backoff.")
    if not cats["429"] and not cats["502"] and not cats["other"]:
        print(f"→ Clean at {args.rpm:g} req/min. Raise --rpm to find a ceiling.")


if __name__ == "__main__":
    main()
