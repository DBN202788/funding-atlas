"""Independent checks on the published dataset. Run after build.py.

These read the built SQLite file and re-derive figures from the source PDFs and
pages by a route separate from the adapters, so a bug in a parser is less likely
to confirm itself. Exits non-zero if any check fails.
"""
import glob
import json
import os
import pathlib
import random
import re
import site
import sqlite3
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.append(site.getusersitepackages())
import pymupdf  # noqa: E402

from fetch import cache_path  # noqa: E402

ROOT = HERE.parent
STAGING = os.environ.get("ATLAS_STAGING")
OUT = pathlib.Path(STAGING) / "out" if STAGING else ROOT / "data" / "out"
WEB = pathlib.Path(STAGING) / "web" if STAGING else ROOT / "web" / "data"
db = sqlite3.connect(OUT / "funding.sqlite")
db.row_factory = sqlite3.Row
failures = []


def check(label, passed, detail=""):
    print(("PASS " if passed else "FAIL ") + label + (f"  [{detail}]" if detail else ""))
    if not passed:
        failures.append(label)


# 1. No duplicate ids, and every record has a source.
total = db.execute("select count(*) from records").fetchone()[0]
unique = db.execute("select count(distinct id) from records").fetchone()[0]
check("record ids are unique", total == unique, f"{total} rows")
missing = db.execute("select count(*) from records where source_url is null or source_url = ''").fetchone()[0]
check("every record links to a source", missing == 0)
bad = db.execute("select count(*) from records where source_url not like 'https://%'").fetchone()[0]
check("every source link is a full https address", bad == 0, f"{bad} bad")

# 2. Delaware per-year sums equal the grand totals printed in each act's synopsis.
PRINTED = {"FY2027": 99407051, "FY2026": 98283625, "FY2025": 98498026, "FY2022": 63248262, "FY2021": 54454798, "FY2019": 52145638}
for year, printed in PRINTED.items():
    parsed = db.execute("select sum(appropriated) from records where state='DE' and fiscal_year=?", (year,)).fetchone()[0]
    check(f"Delaware {year} sum equals printed grand total", parsed == printed, f"{parsed:,} vs {printed:,}")

# 3. Random Florida budget links: the cited number and amount appear in the budget page itself.
random.seed(2026)
url = "https://www.flsenate.gov/Session/Bill/2026E/5001E/BillText/er/PDF"
doc = pymupdf.open(cache_path(url, "florida_gaa"))
sample = db.execute("select * from records where fiscal_year='FY2026-27' and status='Funded' and appropriated > 0").fetchall()
misses = []
for row in random.sample(list(sample), 60):
    form, amount = row["form_number"], f"{row['appropriated']:,}"
    pages = re.search(r"p\. ([\d, ]+)", row["evidence"]).group(1)
    page_numbers = [int(p) for p in pages.split(",") if p.strip()]
    text = " ".join(doc[p - 1].get_text() for p in page_numbers)
    # Forms split across lines of the budget add up, so the sum may not print as one number.
    if not re.search(rf"\(SF\s+{form}\)", text) or (amount not in text and len(page_numbers) == 1):
        misses.append((form, amount, page_numbers))
check("60 random funded Florida records verify against the budget page", len(misses) <= 3, f"misses: {misses}")

# 4. Vetoed records: nothing counted as money in the budget, and each has a veto source.
leak = db.execute("select count(*) from records where status='Vetoed by Governor' and appropriated > 0").fetchone()[0]
check("vetoed records carry no budgeted money", leak == 0)
no_link = db.execute("select count(*) from records where status='Vetoed by Governor' and (veto_url is null or veto_url='')").fetchone()[0]
check("vetoed records link to the veto message", no_link == 0)

# 5. Funded amounts should not exceed the request by a wide margin, which would suggest a misread number.
odd = db.execute("select form_number, name, requested, appropriated from records where state='FL' and appropriated > requested * 3 and requested > 0").fetchall()
print(f"INFO Florida records budgeted above 3x the request: {len(odd)}")
for row in odd[:15]:
    print("     ", dict(row))

# 6. Personal contact details must not be in the published files.
web = "".join(pathlib.Path(p).read_text(encoding="utf-8") for p in glob.glob(str(WEB / "*.json")))
emails = re.findall(r"[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}", web)
phones = re.findall(r"\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}", web)
check("no email addresses in published data", not emails, str(emails[:3]))
check("no phone numbers in published data", not phones, str(phones[:3]))
form_files = glob.glob(str(WEB / "forms_*.json"))
for path in form_files:
    forms = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    leaked = {key for form in forms.values() for key in form if re.search(r"first_name|last_name|email|phone|lobbyist_name|firm", key)}
    check(f"{pathlib.Path(path).name} has no contact fields", not leaked, str(leaked))

print("\nCounts by state and status")
for row in db.execute("select state, status, count(*) n, sum(requested) r, sum(appropriated) a from records group by 1,2 order by 1,3 desc"):
    print(" ", dict(row))
print("\nFAILED: " + ", ".join(failures) if failures else "\nAll checks passed")
sys.exit(1 if failures else 0)
