"""The automatic update job: refresh, check, then publish or hold.

Steps:
  1. Build a new dataset in a staging folder, from fresh copies of the sources.
  2. Run verify.py and compare with the published data.
  3. Publish only if every check passes. Otherwise the current site stays as it is.
  4. Write data/out/review.md, the list of things a person should look at.

Exit codes: 0 published or nothing changed, 1 held back because a check failed.

Usage: python -I pipeline/update.py [--offline]
--offline skips downloads, which is useful for testing.
"""
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
PUBLISHED_WEB = ROOT / "web" / "data"
PUBLISHED_OUT = ROOT / "data" / "out"
STAGING = ROOT / "data" / "staging"
WATCH_FILE = ROOT / "data" / "derived" / "watched_pages.json"

# Publication stops if the data shrinks by more than this, which would suggest a broken parser.
MAX_REMOVED_SHARE = 0.01
# Application rules are written by hand, so a change to these pages needs a person.
WATCHED_PAGES = {
    "Delaware Grant-in-Aid eligibility": "https://legis.delaware.gov/GrantInAid/Information",
    "Delaware applications and dates": "https://legis.delaware.gov/GrantInAid/Home/",
    "Delaware House Bill 190 changes": "https://legis.delaware.gov/Offices/ControllerGeneral/GrantinAid/HB190",
    "Louisiana Joint Rule 17": "https://legis.la.gov/legis/law.aspx?d=113413",
    "Louisiana NGO instructions": "https://legis.la.gov/legis/NGO/NgoInstructions.htm",
    "Florida request form": "https://flsenate.gov/PublishedContent/Session/Appropriations/Links/LocalFundingInitiativeRequestForm_2026_27.pdf",
}


def load_records(folder):
    path = pathlib.Path(folder) / "records.json"
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    statuses = payload["dict"]["status"]
    # Row positions are set in build.py: 3 name, 8 requested, 9 budgeted, 11 status, 18 id.
    return {row[18]: {"name": row[3], "requested": row[8], "budgeted": row[9], "status": statuses[row[11]]} for row in payload["rows"]}


def compare_snapshots(before, after):
    new = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = [i for i in set(before) & set(after) if before[i] != after[i]]
    return {"new": new, "removed": removed, "changed": sorted(changed)}


def safety_problems(before, diff, reports):
    """Return reasons not to publish."""
    problems = []
    if before and len(diff["removed"]) > len(before) * MAX_REMOVED_SHARE:
        problems.append(f"{len(diff['removed'])} of {len(before)} records would disappear, more than the {MAX_REMOVED_SHARE:.0%} limit.")
    errors = [r for r in reports if r.get("status") == "error" and r.get("stage") != "budget_link"]
    for report in errors:
        problems.append(f"{report['state']} {report['fiscal_year']} failed: {report.get('reason')}")
    return problems


def page_fingerprint(body):
    """Hash what a reader sees. Web pages carry session codes that change on every visit."""
    if body[:4] == b"%PDF":
        return hashlib.sha1(body).hexdigest()
    text = body.decode("utf-8", "replace")
    text = re.sub(r"<script.*?</script>|<style.*?</style>|<!--.*?-->|<input[^>]*>|<[^>]+>", " ", text, flags=re.S | re.I)
    return hashlib.sha1(re.sub(r"\s+", " ", text).strip().encode("utf-8")).hexdigest()


def check_watched_pages(offline):
    """Report rule pages whose text changed since the last run."""
    if offline:
        return [], []
    sys.path.insert(0, str(HERE))
    import urllib.request

    from fetch import USER_AGENT

    saved = json.loads(WATCH_FILE.read_text(encoding="utf-8")) if WATCH_FILE.exists() else {}
    changed, unreachable = [], []
    for label, url in WATCHED_PAGES.items():
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=60) as response:
                digest = page_fingerprint(response.read())
        except Exception as error:
            unreachable.append(f"{label}: {error}")
            continue
        if label in saved and saved[label] != digest:
            changed.append(f"{label}: {url}")
        saved[label] = digest
        time.sleep(3)
    WATCH_FILE.parent.mkdir(parents=True, exist_ok=True)
    WATCH_FILE.write_text(json.dumps(saved, indent=1), encoding="utf-8")
    return changed, unreachable


def review_items(reports, stale, watched_changed, watched_unreachable, diff, problems):
    items = []
    for problem in problems:
        items.append(("Publication held back", problem))
    for line in watched_changed:
        items.append(("Rules page changed: update the checklist if needed", line))
    for report in reports:
        fiscal_year, state = report.get("fiscal_year"), report.get("state")
        if report.get("status") == "held_back":
            items.append(("Year held back because totals don't match", f"{state} {fiscal_year}"))
        if report.get("status") == "requests_only":
            items.append(("Budget not linked yet", f"{state} {fiscal_year}: add it in florida_budget.py once it has passed"))
        if report.get("stage") == "budget_link" and report.get("veto_check") == "pending":
            items.append(("Veto message not processed", f"{state} {fiscal_year}"))
        if report.get("veto_uncertain"):
            items.append(("Uncertain vetoes to check by hand", f"{state} {fiscal_year}: {report['veto_uncertain']} records"))
        if report.get("stage") == "forms" and report.get("records", 0) < report.get("listed", 0):
            items.append(("Forms not yet read", f"{state} {fiscal_year}: {report['records']} of {report['listed']}"))
        possible = report.get("outcomes", {}).get("Possible match, check source")
        if possible:
            items.append(("Louisiana matches to check by hand", f"{fiscal_year}: {possible} possible matches"))
        for bill, act in report.get("acts", {}).items():
            if act.get("vetoes_not_placed"):
                items.append(("Veto lines that aren't standard funding lines", f"{state} {bill}: {act['vetoes_not_placed']}"))
        if report.get("refresh_problem"):
            items.append(("Source failed, so saved data was used", f"{state} {fiscal_year}: {report['refresh_problem']}"))
        if report.get("status") in ("unreachable", "reachable_parser_needed"):
            items.append(("State not loaded", f"{state}: {report.get('reason')}"))
    for url, reason in stale:
        items.append(("Source failed, so the last saved copy was used", f"{url}: {reason}"))
    for line in watched_unreachable:
        items.append(("Rules page could not be checked", line))
    if diff["removed"]:
        items.append(("Records removed from the sources", ", ".join(diff["removed"][:30])))
    return items


def write_review(items, diff, published, path):
    lines = [f"# Atlas review list, {time.strftime('%Y-%m-%d')}", "",
             "**Result:** " + ("published" if published else "held back; the site still shows the previous data"), "",
             f"New records: {len(diff['new'])}. Changed: {len(diff['changed'])}. Removed: {len(diff['removed'])}.", ""]
    grouped = {}
    for heading, detail in items:
        grouped.setdefault(heading, []).append(detail)
    for heading, details in grouped.items():
        lines.append(f"## {heading}")
        lines += [f"- {d}" for d in details[:40]]
        if len(details) > 40:
            lines.append(f"- and {len(details) - 40} more")
        lines.append("")
    if not grouped:
        lines.append("Nothing needs attention.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def promote():
    """Move checked staging files into place, one at a time, only after every check passed."""
    PUBLISHED_WEB.mkdir(parents=True, exist_ok=True)
    PUBLISHED_OUT.mkdir(parents=True, exist_ok=True)
    for source, target in ((STAGING / "web", PUBLISHED_WEB), (STAGING / "out", PUBLISHED_OUT)):
        for item in source.iterdir():
            os.replace(item, target / item.name)


def main():
    offline = "--offline" in sys.argv
    shutil.rmtree(STAGING, ignore_errors=True)
    environment = dict(os.environ, ATLAS_STAGING=str(STAGING), PYTHONIOENCODING="utf-8")
    command = [sys.executable, "-I", str(HERE / "build.py")] + ([] if offline else ["--refresh"])
    build = subprocess.run(command, env=environment, capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(build.stdout[-1500:])
    if build.returncode != 0:
        print(build.stderr[-3000:])
        write_review([("Publication held back", "The build crashed: " + build.stderr.strip()[-400:])], {"new": [], "changed": [], "removed": []}, False, PUBLISHED_OUT / "review.md")
        return 1

    meta = json.loads((STAGING / "web" / "meta.json").read_text(encoding="utf-8"))
    before, after = load_records(PUBLISHED_WEB), load_records(STAGING / "web")
    diff = compare_snapshots(before, after)
    problems = safety_problems(before, diff, meta["reports"])

    verify = subprocess.run([sys.executable, "-I", str(HERE / "verify.py")], env=environment, capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(verify.stdout[-1200:])
    if verify.returncode != 0:
        problems.append("verify.py failed: " + "; ".join(line for line in verify.stdout.splitlines() if line.startswith("FAIL"))[:600])

    changed_pages, unreachable_pages = check_watched_pages(offline)
    stale = [(s["url"], s["reason"]) for s in meta.get("stale_sources", [])]
    items = review_items(meta["reports"], stale, changed_pages, unreachable_pages, diff, problems)
    published = not problems

    # Each run records what's new, so the site can point people to fresh applications.
    meta["checked"] = time.strftime("%Y-%m-%d")
    meta["changes"] = {"new": diff["new"][:1000], "new_count": len(diff["new"]), "changed_count": len(diff["changed"]), "removed_count": len(diff["removed"])}
    if published:
        (STAGING / "web" / "meta.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8")
        promote()
    write_review(items, diff, published, PUBLISHED_OUT / "review.md")
    shutil.rmtree(STAGING, ignore_errors=True)

    print(f"{'PUBLISHED' if published else 'HELD BACK'}: {len(diff['new'])} new, {len(diff['changed'])} changed, {len(diff['removed'])} removed, {len(items)} review items")
    for problem in problems:
        print("PROBLEM:", problem)
    return 0 if published else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
