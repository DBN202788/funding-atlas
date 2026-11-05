"""Reads Louisiana requests and saves public acts, run on GitHub's servers."""
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE / "states")]

import louisiana  # noqa: E402


def main():
    lines = []
    for session in louisiana.SESSION_BILLS:
        saved, rows, failures = louisiana.update_requests(session)
        acts = louisiana.update_acts(session)
        amounts = [r["amount"] for r in saved.values() if r.get("amount") is not None]
        lines += [
            f"## {session}",
            f"Directory lists {len(rows)} requests. Saved {len(saved)}. Failures: {len(failures)} {failures[:10]}",
            f"Total requested: ${sum(amounts):,}",
            "Acts: " + ", ".join(f"{bill} {sorted(docs)}" for bill, docs in acts.items()),
        ]
    summary = "\n\n".join(lines)
    print(summary)
    pathlib.Path(os.environ.get("GITHUB_STEP_SUMMARY", "la_fetch.md")).write_text(summary, encoding="utf-8")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
