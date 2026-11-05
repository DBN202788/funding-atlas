"""Maryland adapter: Legislative Bond Initiatives, the capital projects legislators request.

The General Assembly's page lists every request for a session, including unfunded and
withdrawn ones, with sponsors, the amount requested and the funding provided.
Only the table is read. The linked project PDFs aren't downloaded.
"""
import datetime
import html as html_lib
import json
import pathlib
import re
import urllib.parse
import urllib.request

from fetch import RAW, STALE, USER_AGENT
from tagging import categorize, recipient_type

BASE = "https://mgaleg.maryland.gov"
LIST_URL = f"{BASE}/mgawebsite/Budget/BondInitiatives"
LINES_URL = f"{BASE}/mgawebsite/Budget/GetBondLines"
FIRST_YEAR = 2019
COLUMNS = ["Project Name", "Status", "House Sponsors", "Senate Sponsors", "County", "Amount Requested", "Senate Initiative", "House Initiative", "Other Funding", "Total Funding"]
PROGRAM = "Legislative Bond Initiative"
FUNDED, NOT_FUNDED, WITHDRAWN = "Funded", "No funding listed", "Withdrawn"


def money(text):
    digits = re.sub(r"[^\d]", "", text or "")
    return int(digits) if digits else None


def clean(fragment):
    return re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def parse_lines(page):
    """Returns one dict per request. The desktop table is the one with ten columns."""
    rows, seen = [], set()
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, flags=re.S | re.I):
        raw = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, flags=re.S | re.I)
        cells = [clean(c) for c in raw]
        if len(cells) != len(COLUMNS) or cells[0] == COLUMNS[0] or not cells[0]:
            continue
        link = re.search(r'href="([^"]*bond_initiatives[^"]*)"', raw[0])
        key = (cells[0], cells[2], cells[3], cells[5])
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "name": cells[0], "source_status": cells[1], "house": cells[2], "senate": cells[3], "county": cells[4],
            "requested": money(cells[5]), "senate_initiative": money(cells[6]), "house_initiative": money(cells[7]),
            "other": money(cells[8]), "total": money(cells[9]) or 0, "pdf": html_lib.unescape(link.group(1)) if link else None,
        })
    return rows


def sessions(today=None):
    today = today or datetime.date.today()
    return [f"{year}RS" for year in range(today.year, FIRST_YEAR - 1, -1)]


def session_label(session):
    return f"{session[:4]} Regular Session"


def raw_path(session):
    return RAW / "maryland" / f"bond_lines_{session}.html"


def load_session(session, refresh):
    """Reads a session's table, using the saved copy when the site doesn't answer."""
    path = raw_path(session)
    if refresh or not path.exists():
        try:
            data = urllib.parse.urlencode({"id": "", "ys": session}).encode()
            request = urllib.request.Request(LINES_URL, data=data, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=120) as response:
                body = response.read()
            if parse_lines(body.decode("utf-8", "replace")):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(body)
            elif not path.exists():
                return []
        except Exception as error:
            if not path.exists():
                raise
            STALE.append((LINES_URL + "?ys=" + session, str(error)))
    return parse_lines(path.read_text(encoding="utf-8"))


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


def status_of(row):
    if row["source_status"].lower().startswith("withdrawn"):
        return WITHDRAWN
    return FUNDED if row["total"] > 0 else NOT_FUNDED


def build_records(session, rows):
    records, used = [], {}
    for row in rows:
        base = f"MD-{session}-{slug(row['name'])}"
        used[base] = used.get(base, 0) + 1
        status = status_of(row)
        sponsors = "; ".join(s for s in (f"House: {row['house']}" if row["house"] else "", f"Senate: {row['senate']}" if row["senate"] else "") if s)
        evidence = f"The General Assembly's Legislative Bond Initiatives page lists this request as {row['source_status'].lower() or 'filed'}, with ${row['total']:,} in total funding."
        if row["other"]:
            evidence += f" That includes ${row['other']:,} listed as other funding."
        records.append({
            "id": base if used[base] == 1 else f"{base}-{used[base]}",
            "state": "MD",
            "program": PROGRAM,
            "fiscal_year": session_label(session),
            "name": row["name"],
            "organization": None,
            "group": row["county"] or None,
            "category": categorize(row["name"]),
            "recipient_type": recipient_type(row["name"]),
            "sponsor": sponsors or None,
            "requested": row["requested"],
            "appropriated": row["total"] if status == FUNDED else None,
            "vetoed_amount": None,
            "status": status,
            "form_number": None,
            "search_text": row["county"],
            "match_method": "Listed by the General Assembly",
            "evidence": evidence,
            "source_url": BASE + row["pdf"] if row["pdf"] else LIST_URL,
            "budget_url": LIST_URL,
            "veto_url": None,
        })
    return records


def collect(refresh=False):
    records, reports = [], []
    today = datetime.date.today()
    for session in sessions(today):
        year = int(session[:4])
        report = {"state": "MD", "fiscal_year": session_label(session), "source": LIST_URL}
        try:
            # Funding for recent sessions can still change, so only those are read again.
            rows = load_session(session, refresh and year >= today.year - 1)
        except Exception as error:
            report.update(status="error", reason=str(error))
            reports.append(report)
            continue
        if not rows:
            continue
        built = build_records(session, rows)
        records += built
        report.update(status="published", records=len(built), requested=sum(r["requested"] or 0 for r in built), funded=sum(1 for r in built if r["status"] == FUNDED), funded_total=sum(r["appropriated"] or 0 for r in built))
        reports.append(report)
    return records, reports


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    found, summaries = collect(refresh=True)
    print(json.dumps(summaries, indent=1))
