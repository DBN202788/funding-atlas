"""Downloads Florida request form PDFs, one at a time, at the site's crawl delay.

Safe to stop and restart: finished forms are skipped. Forms are visited in a fixed
random order, so a partial download is still a fair sample for early analysis.

The PDFs contain contact details, so data/raw/florida_forms is never committed or
published. Only pipeline/states/florida_forms.py output leaves this machine.

Usage: python -I pipeline/download_forms.py FY2026-27 [max_forms] [max_minutes]
On GitHub, it reports how many forms it read and how many remain, so the job can
run itself again until every form has been read.
"""
import json
import os
import pathlib
import random
import sys
import time
import urllib.error

HERE = pathlib.Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE / "states")]

import florida  # noqa: E402
from fetch import cache_path, fetch  # noqa: E402

BACKOFF_SECONDS = {429: 310, 503: 120, 522: 120, 524: 120, 520: 120}
MAX_CONSECUTIVE_FAILURES = 12


def latest_year():
    """The newest fiscal year whose request list has rows, checking the site for a new one."""
    for fiscal_year in reversed(florida.fiscal_years()):
        url = f"{florida.BASE}/Session/Appropriations/{fiscal_year}/LocalFundingRequests"
        try:
            page = fetch(url, "florida", refresh=True).decode("utf-8", "replace")
        except Exception:
            continue
        if florida.parse_listing(page, fiscal_year):
            return fiscal_year
    raise SystemExit("No request list could be read.")


def report_progress(done, remaining):
    """Lets a GitHub workflow decide whether to run again."""
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"done={done}\nremaining={remaining}\n")


def download(fiscal_year, limit=None, max_minutes=None):
    if fiscal_year == "latest":
        fiscal_year = latest_year()
    url = f"{florida.BASE}/Session/Appropriations/{fiscal_year}/LocalFundingRequests"
    rows = florida.parse_listing(fetch(url, "florida").decode("utf-8", "replace"), fiscal_year)
    random.Random(2026).shuffle(rows)
    # Forms already read need no download, which matters on update servers that don't keep the PDFs.
    derived = HERE.parent / "data" / "derived" / f"fl_forms_{fiscal_year}.json"
    read = set(json.loads(derived.read_text(encoding="utf-8"))) if derived.exists() else set()
    pending = [r for r in rows if r["form_number"] not in read and not cache_path(r["source_url"], "florida_forms").exists()]
    print(f"{fiscal_year}: {len(rows)} forms, {len(pending)} to download", flush=True)

    deadline = time.monotonic() + max_minutes * 60 if max_minutes else None
    done, failed, consecutive = 0, [], 0
    for row in pending[:limit]:
        if deadline and time.monotonic() > deadline:
            print("Time budget used. The next run continues from here.", flush=True)
            break
        try:
            body = fetch(row["source_url"], "florida_forms")
            if body[:4] != b"%PDF":
                cache_path(row["source_url"], "florida_forms").unlink(missing_ok=True)
                raise ValueError("response was not a PDF")
            done += 1
            consecutive = 0
            if done % 100 == 0:
                print(f"{done} downloaded, {len(failed)} failed", flush=True)
        except urllib.error.HTTPError as error:
            failed.append(row["form_number"])
            consecutive += 1
            print(f"form {row['form_number']}: HTTP {error.code}", flush=True)
            time.sleep(BACKOFF_SECONDS.get(error.code, 30))
        except Exception as error:
            failed.append(row["form_number"])
            consecutive += 1
            print(f"form {row['form_number']}: {error}", flush=True)
            time.sleep(60)
        if consecutive >= MAX_CONSECUTIVE_FAILURES:
            print("Stopping after repeated failures. Rerun later to resume.", flush=True)
            break
    print(f"finished: {done} downloaded, {len(failed)} failed: {failed[:30]}", flush=True)
    report_progress(done, len(pending) - done)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    download(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else None, float(sys.argv[3]) if len(sys.argv) > 3 else None)
