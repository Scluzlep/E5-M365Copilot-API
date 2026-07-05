"""GPQA accuracy benchmark — scores the running server on multiple-choice Q&A.

Loads the GPQA dataset (https://github.com/idavidrein/gpqa — access-gated, you
must obtain the CSV yourself), turns each row into a shuffled A/B/C/D question,
sends it to the OpenAI-compatible server one at a time, parses the answer
letter, and reports accuracy.

    # 1. Start the server in another terminal
    python app.py

    # 2. Validate parsing on a few questions before a full run
    python tests/gpqa_bench.py path/to/gpqa_diamond.csv --limit 10

    # 3. Full run, writing a per-question log + final score to JSON
    python tests/gpqa_bench.py path/to/gpqa_diamond.csv --out results.json

Notes specific to this project:
  * The server serializes upstream Copilot calls behind a single lock
    (see server/api.py), so this runs strictly sequentially. A --delay between
    questions keeps things gentle on your account; please don't hammer it.
  * Consumer Copilot browses the web, so these scores measure
    "Copilot-with-search", not a closed-book model — not comparable to the
    GPQA paper's numbers. Label your results accordingly.
  * Answers are scored by extracting the letter from free text, so a refusal
    or off-format reply counts as wrong (logged as picked="?").
"""

import argparse
import csv
import json
import random
import re
import time
import urllib.error
import urllib.request

PROMPT = """Answer the following multiple-choice question. Respond with ONLY the \
letter (A, B, C, or D) of the correct option on the first line.

{q}

A) {a}
B) {b}
C) {c}
D) {d}"""

LETTERS = "ABCD"

# Column names in the GPQA CSVs. Override on the command line if your copy
# differs (some exports prefix or rename these).
COL_QUESTION = "Question"
COL_CORRECT = "Correct Answer"
COL_INCORRECT = ["Incorrect Answer 1", "Incorrect Answer 2", "Incorrect Answer 3"]


def build_question(row, rng):
    """Return (prompt, correct_letter) for one CSV row, options shuffled."""
    correct = row[COL_CORRECT].strip()
    options = [correct] + [row[c].strip() for c in COL_INCORRECT]
    rng.shuffle(options)
    correct_letter = LETTERS[options.index(correct)]
    prompt = PROMPT.format(
        q=row[COL_QUESTION].strip(),
        a=options[0], b=options[1], c=options[2], d=options[3],
    )
    return prompt, correct_letter


def parse_letter(text):
    """Extract the chosen letter from the reply, or '?' if none is found.

    Prefers a letter on its own / at the very start (e.g. "A", "A)", "A."),
    and falls back to the first standalone A-D anywhere in the text.
    """
    head = text.strip()
    m = re.match(r"\s*\(?([ABCD])\b", head)
    if m:
        return m.group(1)
    m = re.search(r"\b([ABCD])\b", head)
    return m.group(1) if m else "?"


def ask(endpoint, timeout, prompt):
    """Send one chat completion. Returns (reply_text, detail_or_none)."""
    body = json.dumps({
        "model": "copilot",
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        if "error" in payload:
            return None, f"error payload: {payload['error']}"
        return payload["choices"][0]["message"]["content"], None
    except urllib.error.HTTPError as exc:
        return None, f"HTTP {exc.code}: {exc.reason}"
    except Exception as exc:  # timeout, connection reset, malformed body, ...
        return None, f"{type(exc).__name__}: {exc}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", help="Path to the GPQA CSV file")
    parser.add_argument(
        "--url", default="http://localhost:8000",
        help="Server base URL (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--limit", type=int, default=0,
        help="Only run the first N questions (default: 0 = all)",
    )
    parser.add_argument(
        "--delay", type=float, default=2.0,
        help="Seconds to wait between questions (default: 2.0)",
    )
    parser.add_argument(
        "--timeout", type=float, default=180,
        help="Per-request timeout in seconds (default: 180)",
    )
    parser.add_argument(
        "--seed", type=int, default=0,
        help="RNG seed for option shuffling, for reproducibility (default: 0)",
    )
    parser.add_argument(
        "--out", default=None,
        help="Write per-question log + final score to this JSON file",
    )
    args = parser.parse_args()

    endpoint = args.url.rstrip("/") + "/v1/chat/completions"
    rng = random.Random(args.seed)

    with open(args.csv, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        parser.error(f"No rows found in {args.csv}")
    missing = [c for c in [COL_QUESTION, COL_CORRECT, *COL_INCORRECT]
               if c not in rows[0]]
    if missing:
        parser.error(
            f"CSV is missing expected column(s): {missing}. "
            f"Found columns: {list(rows[0])}"
        )
    if args.limit:
        rows = rows[:args.limit]

    print(f"GPQA benchmark against {endpoint}")
    print(f"{len(rows)} question(s), seed={args.seed}, delay={args.delay}s\n")

    log = []
    correct = 0
    start = time.perf_counter()
    try:
        for i, row in enumerate(rows, 1):
            prompt, gold = build_question(row, rng)
            reply, detail = ask(endpoint, args.timeout, prompt)
            picked = parse_letter(reply) if reply is not None else "?"
            ok = picked == gold
            correct += ok

            log.append({
                "index": i,
                "picked": picked,
                "gold": gold,
                "correct": ok,
                "error": detail,
                "reply": (reply or "").strip()[:500],
            })
            flag = "✓" if ok else "✗"
            note = f"  ({detail})" if detail else ""
            print(f"[{i}/{len(rows)}] {flag} picked={picked} gold={gold}  "
                  f"acc={correct / i:.1%}{note}")

            if i < len(rows):
                time.sleep(args.delay)
    except KeyboardInterrupt:
        print("\nInterrupted — reporting partial results.")

    answered = len(log)
    wall = time.perf_counter() - start
    accuracy = correct / answered if answered else 0.0
    print(f"\nFinal: {correct}/{answered} = {accuracy:.1%}  "
          f"(wall {wall:.0f}s)")

    if args.out:
        summary = {
            "csv": args.csv,
            "endpoint": endpoint,
            "seed": args.seed,
            "answered": answered,
            "correct": correct,
            "accuracy": accuracy,
            "wall_seconds": round(wall, 1),
            "results": log,
        }
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)
        print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
