"""Builds the dataset from every state adapter.

Run from the project root:  python -I pipeline/build.py [--refresh]

Normally writes to data/out and web/data. When ATLAS_STAGING is set, everything
goes under that folder instead, so update.py can check it before publishing.
"""
import csv
import json
import os
import pathlib
import sqlite3
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE / "states")]

import compare  # noqa: E402
import delaware  # noqa: E402
import fetch as fetch_module  # noqa: E402
import florida  # noqa: E402
import florida_budget  # noqa: E402
import florida_forms  # noqa: E402
import louisiana  # noqa: E402

ROOT = HERE.parent
STAGING = os.environ.get("ATLAS_STAGING")
OUT = pathlib.Path(STAGING) / "out" if STAGING else ROOT / "data" / "out"
WEB = pathlib.Path(STAGING) / "web" if STAGING else ROOT / "web" / "data"
FORM_YEARS = ["FY2026-27"]
CSV_FIELDS = [
    "id", "state", "program", "fiscal_year", "name", "group", "category", "recipient_type", "organization", "sponsor",
    "requested", "appropriated", "vetoed_amount", "status", "form_number", "match_method", "evidence",
    "source_url", "budget_url", "veto_url", "budget_text",
]
FORM_TYPE_LABEL = {
    "Nonprofit 501(c)(3)": "Nonprofit 501(c)(3) (from form)",
    "Nonprofit 501(c)(4)": "Nonprofit 501(c)(4) (from form)",
    "Local government or entity": "Local government or entity (from form)",
    "University or college": "University or college (from form)",
    "For-profit": "For-profit (from form)",
    "Other": "Other (from form)",
}


def apply_forms(records):
    """Replace keyword guesses with what applicants marked on their forms."""
    all_forms, comparisons, form_reports = {}, [], []
    fl_years = {r["fiscal_year"] for r in records if r["state"] == "FL"}
    derived = {p.stem.replace("fl_forms_", "") for p in florida_forms.DERIVED.glob("fl_forms_*.json")} if florida_forms.DERIVED.exists() else set()
    raw_years = {y for y in fl_years if any(florida_forms.cache_path(r["source_url"], "florida_forms").exists() for r in records if r["fiscal_year"] == y and r["state"] == "FL")}
    newest = sorted(fl_years)[-1:]
    for fiscal_year in sorted((set(FORM_YEARS) | derived | raw_years | set(newest)) & fl_years, reverse=True):
        year_records = [r for r in records if r["fiscal_year"] == fiscal_year]
        forms, errors = florida_forms.extract_year(fiscal_year, year_records)
        all_forms[fiscal_year] = forms
        for record in year_records:
            form = forms.get(record["form_number"])
            if not form:
                continue
            if form.get("organization"):
                record["organization"] = form["organization"]
            if form.get("org_type") in FORM_TYPE_LABEL:
                record["recipient_type"] = FORM_TYPE_LABEL[form["org_type"]]
        comparisons.append(compare.compare(records, forms, fiscal_year))
        form_reports.append({"state": "FL", "fiscal_year": fiscal_year, "stage": "forms", "status": "published",
                             "records": len(forms), "listed": len(year_records), "parse_errors": len(errors), "errors": errors[:10]})
    return all_forms, comparisons, form_reports


def collect_all(refresh=False):
    records, reports = [], []
    fl_records, fl_reports = florida.collect(refresh=refresh)
    reports += fl_reports
    reports += [dict(r, stage="budget_link") for r in florida_budget.link(fl_records, look_for_new_budgets=refresh)]
    records += fl_records
    de_records, de_reports = delaware.collect(refresh=refresh)
    records += de_records
    reports += de_reports
    la_records, la_reports = louisiana.collect()
    records += la_records
    reports += la_reports
    all_forms, comparisons, form_reports = apply_forms(records)
    reports += form_reports
    return records, reports, all_forms, comparisons


def write_exports(records):
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / "funding.csv", "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)

    db_path = OUT / "funding.sqlite"
    db_path.unlink(missing_ok=True)
    connection = sqlite3.connect(db_path)
    columns = ", ".join(f'"{field}"' for field in CSV_FIELDS)
    connection.execute(f"CREATE TABLE records ({columns})")
    marks = ", ".join("?" for _ in CSV_FIELDS)
    connection.executemany(
        f"INSERT INTO records VALUES ({marks})",
        [[record.get(field) for field in CSV_FIELDS] for record in records],
    )
    connection.execute('CREATE INDEX idx_state_year ON records ("state", "fiscal_year")')
    connection.commit()
    connection.close()


class Dictionary:
    """Stores repeated strings once so the browser download stays small."""

    def __init__(self):
        self.values, self.index = [], {}

    def add(self, value):
        if value is None:
            return -1
        if value not in self.index:
            self.index[value] = len(self.values)
            self.values.append(value)
        return self.index[value]


def write_web(records, reports, all_forms, comparisons):
    WEB.mkdir(parents=True, exist_ok=True)
    names = ["state", "program", "year", "group", "category", "type", "sponsor", "status", "budget", "veto", "prefix"]
    dictionaries = {name: Dictionary() for name in names}
    rows = []
    for record in records:
        url = record["source_url"]
        cut = url.rfind("/") + 1
        rows.append([
            dictionaries["state"].add(record["state"]),
            dictionaries["program"].add(record["program"]),
            dictionaries["year"].add(record["fiscal_year"]),
            record["name"],
            dictionaries["group"].add(record.get("group")),
            dictionaries["category"].add(record["category"]),
            dictionaries["type"].add(record["recipient_type"]),
            dictionaries["sponsor"].add(record.get("sponsor")),
            record.get("requested"),
            record.get("appropriated"),
            record.get("vetoed_amount"),
            dictionaries["status"].add(record["status"]),
            record.get("form_number"),
            record["evidence"],
            dictionaries["prefix"].add(url[:cut]),
            url[cut:],
            dictionaries["budget"].add(record.get("budget_url")),
            dictionaries["veto"].add(record.get("veto_url")),
            record["id"],
            record.get("organization"),
        ])
    payload = {"dict": {name: dictionaries[name].values for name in names}, "rows": rows}
    (WEB / "records.json").write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    for fiscal_year, forms in all_forms.items():
        (WEB / f"forms_{fiscal_year}.json").write_text(json.dumps(forms, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    (WEB / "compare.json").write_text(json.dumps(comparisons, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")

    meta = {
        "generated": time.strftime("%Y-%m-%d"),
        "record_count": len(records),
        "form_years": sorted((y for y, forms in all_forms.items() if forms), reverse=True),
        "reports": reports,
        "stale_sources": [{"url": u, "reason": r} for u, r in fetch_module.STALE],
    }
    (WEB / "meta.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False, default=str), encoding="utf-8")


def main(refresh=False):
    records, reports, all_forms, comparisons = collect_all(refresh=refresh)
    write_exports(records)
    write_web(records, reports, all_forms, comparisons)
    print("records", len(records))
    for report in reports:
        summary = {k: report.get(k) for k in ("state", "fiscal_year", "stage", "status", "records", "forms_linked", "vetoed", "veto_uncertain", "reason") if report.get(k) is not None}
        print(summary)
    return records, reports


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main(refresh="--refresh" in sys.argv)
