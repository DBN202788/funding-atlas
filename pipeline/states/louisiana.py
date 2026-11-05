"""Louisiana adapter: NGO funding requests and the acts that fund them.

Request pages list board members' and officers' names and addresses. Those pages
are read in memory and never saved. Only the fields in KEEP_FIELDS are extracted,
so anything personal is left out by design, not removed afterward.

legis.la.gov doesn't resolve from every machine, so requests are stored in
data/derived and the public acts in data/raw/louisiana_acts. A machine that can't
reach the site can still rebuild from those saved copies.
"""
import datetime
import html as html_lib
import json
import pathlib
import re
import urllib.request

from fetch import STALE, USER_AGENT, fetch

ROOT = pathlib.Path(__file__).resolve().parents[2]
DERIVED = ROOT / "data" / "derived"
BASE = "https://www.legis.la.gov/legis"
REQUEST_DELAY_SECONDS = 3
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE = re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?!\d)")

# Page field id -> saved name. Officers, incorporators, addresses, contacts, tax IDs and
# the state-official relationship answers are deliberately absent.
KEEP_FIELDS = {
    "LabelVEntityName": "name",
    "LabelVEntityType": "entity_type",
    "LabelVRequestAmount": "amount",
    "LabelVRequestType": "request_type",
    "LabelVSOSStanding": "good_standing",
    "LabelVProjectSummary": "summary",
    "LabelVBudgetSalary": "budget_salaries",
    "LabelVBudgetProfServ": "budget_professional",
    "LabelVBudgetContracts": "budget_contracts",
    "LabelVBudgetAcq": "budget_acquisitions",
    "LabelVBudgetRepairs": "budget_repairs",
    "LabelVBudgetOpServices": "budget_operating",
    "LabelVBudgetOther": "budget_other",
    "LabelVAuditIssues": "audit_issues",
    "LabelVPublicPurpose": "purpose",
    "LabelVGoalsAndObjectives": "goals",
    "LabelVTimeEstimate": "time_estimate",
}
MONEY_FIELDS = {"amount", "budget_salaries", "budget_professional", "budget_contracts", "budget_acquisitions", "budget_repairs", "budget_operating", "budget_other"}
TEXT_LIMITS = {"summary": 900, "purpose": 500, "goals": 700, "time_estimate": 120}

# Budget bills to read for each session. A new session needs its supplemental bill added.
SESSION_BILLS = {"26RS": ["HB1", "HB2", "HB312", "HB313", "HB314", "HB3"]}
SESSION_LABEL = {"26RS": "2026 Regular Session"}


def sessions(today=None):
    today = today or datetime.date.today()
    return [f"{str(y)[2:]}RS" for y in range(2026, today.year + 2)]


def directory_url(session):
    return f"{BASE}/NGO/NgoSearch.aspx?s={session}"


def request_url(ngo_id):
    return f"{BASE}/NGO/NgoDoc.aspx?NgoId={ngo_id}&search=ALL"


def parse_directory(page):
    """Return [(ngo_id, listed_name)] from a session's directory page."""
    rows = []
    for ngo_id, name in re.findall(r'href="NgoDoc\.aspx\?NgoId=(\d+)[^"]*"[^>]*>(.*?)</a>', page, re.S):
        rows.append((ngo_id, re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", " ", name))).strip()))
    return rows


def clean_text(value):
    value = re.sub(r"<br\s*/?>", " ", value, flags=re.I)
    value = html_lib.unescape(re.sub(r"<[^>]+>", " ", value))
    value = PHONE.sub("[phone removed]", EMAIL.sub("[email removed]", value))
    return re.sub(r"\s+", " ", value).strip()


def parse_request(page):
    """Extract only the kept fields from a request page."""
    result = {}
    for field_id, name in KEEP_FIELDS.items():
        match = re.search(rf'<span[^>]*id="[^"]*{field_id}"[^>]*>(.*?)</span>', page, re.S)
        text = clean_text(match.group(1)) if match else ""
        if name in MONEY_FIELDS:
            digits = re.sub(r"[^\d.]", "", text)
            result[name] = int(float(digits)) if digits else None
        else:
            result[name] = text[:TEXT_LIMITS[name]] if name in TEXT_LIMITS else text
    return result


def requests_path(session):
    return DERIVED / f"la_requests_{session}.json"


def load_requests(session):
    path = requests_path(session)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def update_requests(session, refresh_all=False):
    """Read any request not already saved. Returns (saved, directory_rows, failures)."""
    import time

    rows = parse_directory(fetch(directory_url(session), "louisiana", refresh=True).decode("utf-8", "replace"))
    saved = load_requests(session)
    failures = []
    for ngo_id, listed in rows:
        if ngo_id in saved and not refresh_all:
            continue
        try:
            # Read into memory only. This page must not reach data/raw.
            request = urllib.request.Request(request_url(ngo_id), headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=60) as response:
                page = response.read().decode("utf-8", "replace")
            record = parse_request(page)
            record["listed_name"] = listed
            if not record["name"] or record["amount"] is None:
                failures.append((ngo_id, "missing name or amount"))
            saved[ngo_id] = record
        except Exception as error:
            failures.append((ngo_id, str(error)))
        time.sleep(REQUEST_DELAY_SECONDS)
    if rows:
        DERIVED.mkdir(parents=True, exist_ok=True)
        requests_path(session).write_text(json.dumps(saved, ensure_ascii=False, indent=0), encoding="utf-8")
    return saved, rows, failures


def bill_url(session, bill):
    return f"{BASE}/BillInfo.aspx?s={session}&b={bill}&sbi=y"


def bill_documents(page):
    """Find the signed act and veto message links on a bill's page."""
    documents = {}
    for href, text in re.findall(r'<a[^>]+href="([^"]*ViewDocument[^"]*)"[^>]*>(.*?)</a>', page, re.S):
        label = re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", " ", text))).strip()
        url = BASE + "/" + html_lib.unescape(href).lstrip("./")
        act = re.match(r"\w+ Act (\d+)$", label)
        if act:
            documents["act"] = (url, act.group(1))
        elif label.endswith("Veto Message"):
            documents["veto"] = (url, None)
    return documents


def update_acts(session):
    """Save each bill's signed act and veto message, which are public documents."""
    found = {}
    for bill in SESSION_BILLS.get(session, []):
        try:
            page = fetch(bill_url(session, bill), "louisiana_bills", refresh=True).decode("utf-8", "replace")
            documents = bill_documents(page)
            for kind, (url, number) in documents.items():
                fetch(url, "louisiana_acts")
            found[bill] = {k: {"url": u, "number": n} for k, (u, n) in documents.items()}
        except Exception as error:
            found[bill] = {"error": str(error)}
    DERIVED.mkdir(parents=True, exist_ok=True)
    (DERIVED / f"la_acts_{session}.json").write_text(json.dumps(found, indent=1), encoding="utf-8")
    return found


def collect(refresh=False):
    """Refresh saved requests and acts where possible. Records are added once matching is checked."""
    reports = []
    for session in sessions():
        known = bool(load_requests(session)) or session in SESSION_BILLS
        if not known and not refresh:
            continue
        report = {"state": "LA", "fiscal_year": SESSION_LABEL.get(session, session), "source": directory_url(session)}
        if refresh:
            try:
                saved, rows, failures = update_requests(session)
                if not rows and not saved:
                    continue  # This session's directory doesn't exist yet.
                update_acts(session)
                report.update(status="reachable_parser_needed", records=len(saved), listed=len(rows), failures=failures[:10], reason=f"{len(saved)} requests read; matching to acts is being checked")
            except Exception as error:
                if session not in SESSION_BILLS:
                    continue
                report.update(status="unreachable", reason=str(error))
        else:
            saved = load_requests(session)
            report.update(status="reachable_parser_needed", records=len(saved), reason=f"{len(saved)} requests saved; matching to acts is being checked")
        reports.append(report)
    return [], reports
