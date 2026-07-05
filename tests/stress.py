"""Concurrency stress test — doubles the parallel request count each round.

Fires a batch of simultaneous requests at the running server. If every request
in the batch succeeds, the batch size is doubled and the next round runs. The
test stops at the first round that produces any error (HTTP error, timeout,
connection failure, or an error payload in the response), and reports the last
batch size that fully succeeded.

Note: the server serializes upstream Copilot calls behind a single lock
(see server/api.py), so concurrent requests queue and run one at a time. This
test therefore probes how the server copes with a growing *queue* of waiting
connections (socket/timeout limits), not true upstream parallelism.

    # 1. Start the server in another terminal
    python app.py

    # 2. Run the stress test from the project root
    python tests/stress.py
    python tests/stress.py --max 64 --timeout 120 --url http://localhost:8000

Be considerate: this hammers your Copilot account. Keep --max modest.
"""

import argparse
import json
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

PROMPT = "Reply with a single word: ok"


def one_request(url, timeout, index):
    """Send a single chat completion. Returns (ok, elapsed, detail)."""
    body = json.dumps({
        "model": "copilot",
        "messages": [{"role": "user", "content": PROMPT}],
    }).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        elapsed = time.perf_counter() - start
        # The server returns HTTP 200 with an "error" object on upstream
        # failures, so inspect the body too — not just the status code.
        if "error" in payload:
            return False, elapsed, f"error payload: {payload['error']}"
        content = payload["choices"][0]["message"]["content"]
        return True, elapsed, content.strip()[:40]
    except urllib.error.HTTPError as exc:
        return False, time.perf_counter() - start, f"HTTP {exc.code}: {exc.reason}"
    except Exception as exc:  # timeout, connection reset, malformed body, ...
        return False, time.perf_counter() - start, f"{type(exc).__name__}: {exc}"


def run_round(url, timeout, concurrency):
    """Fire `concurrency` requests at once. Returns (results, wall_seconds)."""
    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [
            pool.submit(one_request, url, timeout, i) for i in range(concurrency)
        ]
        results = [f.result() for f in as_completed(futures)]
    return results, time.perf_counter() - start


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url", default="http://localhost:8000",
        help="Server base URL (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--start", type=int, default=1,
        help="Starting concurrency (default: 1)",
    )
    parser.add_argument(
        "--max", type=int, default=128,
        help="Stop after this batch size even if it succeeds (default: 128)",
    )
    parser.add_argument(
        "--timeout", type=float, default=180,
        help="Per-request timeout in seconds (default: 180)",
    )
    parser.add_argument(
        "--pause", type=float, default=1.0,
        help="Seconds to wait between rounds (default: 1.0)",
    )
    args = parser.parse_args()

    endpoint = args.url.rstrip("/") + "/v1/chat/completions"
    print(f"Stress testing {endpoint}")
    print(f"Doubling concurrency from {args.start} up to {args.max}, "
          f"timeout {args.timeout}s\n")

    concurrency = args.start
    last_good = 0
    try:
        while concurrency <= args.max:
            print(f"── Round: {concurrency} concurrent "
                  f"request{'s' if concurrency > 1 else ''} ──")
            results, wall = run_round(endpoint, args.timeout, concurrency)

            oks = [r for r in results if r[0]]
            fails = [r for r in results if not r[0]]
            latencies = sorted(r[1] for r in results)
            lo, hi = latencies[0], latencies[-1]
            mid = latencies[len(latencies) // 2]

            print(f"   ok={len(oks)}  failed={len(fails)}  wall={wall:.1f}s")
            print(f"   latency  min={lo:.1f}s  median={mid:.1f}s  max={hi:.1f}s")

            if fails:
                print(f"\n✗ {len(fails)} request(s) failed at concurrency "
                      f"{concurrency}. Sample errors:")
                for _, elapsed, detail in fails[:5]:
                    print(f"     [{elapsed:.1f}s] {detail}")
                break

            last_good = concurrency
            print(f"   ✓ all {concurrency} succeeded\n")
            concurrency *= 2
            time.sleep(args.pause)
        else:
            print(f"\nReached --max={args.max} with no errors.")
    except KeyboardInterrupt:
        print("\nInterrupted.")

    print(f"\nHighest fully-successful concurrency: {last_good}")


if __name__ == "__main__":
    main()
