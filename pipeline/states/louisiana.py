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

import louisiana_acts
from fetch import USER_AGENT, fetch
from tagging import categorize

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


TYPE_LABEL = {
    "Non-Profit Corporation": "Nonprofit corporation (from form)",
    "Non-Profit Religious Corp.": "Nonprofit religious corporation (from form)",
    "Limited Liability Company": "Limited liability company (from form)",
    "Association": "Association (from form)",
    "Corporation": "Corporation (from form)",
}
STATUS_FUNDED = "Appropriated (name match)"
STATUS_POSSIBLE = "Possible match, check source"
STATUS_NONE = "No appropriation found by name"
STATUS_VETOED = "Vetoed by Governor"


def entry_wording(entry):
    """The act's own words, without the repeated funding-source opening."""
    text = re.sub(r"^Payable out of .*?(?=\bto\s|\bthe city\b|\bthe town\b|\bthe village\b|\bfor\s)", "", entry["text"])
    return text.strip()[:220]


def is_shared(entry):
    """A line split among several recipients can't be credited in full to any one of them."""
    return bool(re.search(r"\bpro rata\b|\bthe following\b", entry["text"], re.I))


def build_records(session, requests, entries, review):
    """Create one record per request, counting each budget line only once."""
    excluded = {k.lower(): v for k, v in review.get("exclude_requests", {}).items()}
    downgrade = {k.lower(): v for k, v in review.get("downgrade_to_possible", {}).items()}
    usable = {k: r for k, r in requests.items() if (r.get("name") or "").strip().lower() not in excluded}
    matches = louisiana_acts.match_requests(usable, entries)
    mentions = louisiana_acts.text_mentions(usable, matches, session)
    claimed = set()
    records, details = [], {}
    # Larger requests come first, so a repeat applicant's money lands on its main request.
    for ngo_id, request in sorted(usable.items(), key=lambda kv: -(kv[1].get("amount") or 0)):
        found = matches[ngo_id]
        confirmed, possible = found["confirmed"], found["possible"]
        note = ""
        if (request.get("name") or "").strip().lower() in downgrade:
            note = downgrade[request["name"].strip().lower()]
            possible, confirmed = confirmed + possible, []

        budgeted, vetoed, lines, already_counted, shared = 0, 0, [], False, False
        for entry in confirmed:
            key = (entry["bill"], entry["page"], entry["start"])
            line_shared = is_shared(entry)
            first_claim = key not in claimed
            if first_claim:
                claimed.add(key)
            else:
                already_counted = True
            if line_shared:
                shared = True
            if first_claim and not line_shared:
                budgeted += 0 if entry["fully_vetoed"] else entry["amount"]
                vetoed += entry.get("vetoed_amount") or 0
            lines.append({
                "act": louisiana_acts.BILL_LABEL[entry["bill"]], "page": entry["page"], "lines": f"{entry['start']}-{entry['end']}",
                "amount": entry["amount"],
                "vetoed": entry["fully_vetoed"], "shared": line_shared, "wording": entry_wording(entry), "url": entry["act_url"],
            })
        all_vetoed = bool(confirmed) and all(e["fully_vetoed"] for e in confirmed)
        evidence_lines = "; ".join(f"{x['act']} p. {x['page']}, lines {x['lines']}: \"{x['wording'][:90]}\"{' (vetoed)' if x['vetoed'] else ''}" for x in lines[:4])

        if all_vetoed:
            status, evidence = STATUS_VETOED, f"Organization named in {evidence_lines}. Removed by the Governor's veto letter."
        elif confirmed:
            status, evidence = STATUS_FUNDED, f"Organization named in {evidence_lines}."
            if any(x["vetoed"] for x in lines):
                evidence += " Some of its lines were vetoed."
        elif possible:
            near = "; ".join(f"{louisiana_acts.BILL_LABEL[e['bill']]} p. {e['page']}: \"{entry_wording(e)[:90]}\"" for e in possible[:3])
            status, evidence = STATUS_POSSIBLE, f"Similar name, but not an exact match: {near}. {note}".strip()
        elif mentions.get(ngo_id):
            status, evidence = STATUS_POSSIBLE, "Name appears in the act's text outside a standard funding line: " + "; ".join(mentions[ngo_id][:3]) + ". Read the act to see what it says."
        else:
            status, evidence = STATUS_NONE, "The organization's name wasn't found in Act 3, Act 4 or Act 961. It could still be funded under other wording."
        if shared:
            evidence += " At least one line is shared among several recipients, so its amount isn't credited here."
        if already_counted:
            evidence += " Some lines are counted on another request from the same organization."

        name = request.get("listed_name") or request.get("name")
        records.append({
            "id": f"LA-{session}-{ngo_id}",
            "state": "LA",
            "program": "NGO Funding Request",
            "fiscal_year": SESSION_LABEL.get(session, session),
            "name": name,
            "organization": request.get("name"),
            "group": request.get("request_type"),
            "category": categorize(name, request.get("summary"), request.get("purpose")),
            "recipient_type": TYPE_LABEL.get(request.get("entity_type"), "Other (from form)"),
            "sponsor": None,
            "requested": request.get("amount"),
            "appropriated": (budgeted if confirmed and budgeted else None) if not all_vetoed else 0,
            "vetoed_amount": vetoed or None,
            "status": status,
            "form_number": ngo_id,
            "search_text": (request.get("summary") or "")[:300],
            "match_method": "Organization name",
            "evidence": evidence,
            "source_url": request_url(ngo_id),
            "budget_url": confirmed[0]["act_url"] if confirmed else None,
            "veto_url": next((e["veto_url"] for e in confirmed if e["fully_vetoed"]), None),
        })
        details[ngo_id] = {k: request.get(k) for k in ("entity_type", "request_type", "good_standing", "audit_issues", "summary", "purpose", "goals", "time_estimate", "budget_salaries", "budget_professional", "budget_contracts", "budget_acquisitions", "budget_repairs", "budget_operating", "budget_other")}
        details[ngo_id]["budget_lines"] = lines
    return records, details, excluded


def collect(refresh=False):
    """Refresh saved requests and acts where possible, then match them."""
    records, reports = [], []
    review_path = ROOT / "data" / "manual" / "la_review.json"
    review = json.loads(review_path.read_text(encoding="utf-8")) if review_path.exists() else {}
    global LA_DETAILS
    LA_DETAILS = {}
    for session in sessions():
        known = bool(load_requests(session)) or session in SESSION_BILLS
        if not known and not refresh:
            continue
        report = {"state": "LA", "fiscal_year": SESSION_LABEL.get(session, session), "source": directory_url(session)}
        listed = None
        if refresh:
            try:
                saved, rows, failures = update_requests(session)
                if not rows and not saved:
                    continue  # This session's directory doesn't exist yet.
                listed = len(rows)
                report["failures"] = failures[:10]
                if session in SESSION_BILLS:
                    update_acts(session)
            except Exception as error:
                if session not in SESSION_BILLS:
                    continue
                report["refresh_problem"] = str(error)
        requests = load_requests(session)
        if not requests:
            report.update(status="unreachable", reason=report.get("refresh_problem", "No saved requests yet"))
            reports.append(report)
            continue
        if session not in SESSION_BILLS:
            report.update(status="reachable_parser_needed", records=len(requests), reason=f"{session} needs its budget bills added to SESSION_BILLS")
            reports.append(report)
            continue
        entries, act_report = louisiana_acts.load_all_entries(session)
        session_records, details, excluded = build_records(session, requests, entries, review)
        records += session_records
        LA_DETAILS[session] = details
        counts = {}
        for record in session_records:
            counts[record["status"]] = counts.get(record["status"], 0) + 1
        report.update(status="published", records=len(session_records), listed=listed or len(requests), excluded=len(excluded), outcomes=counts, acts=act_report)
        reports.append(report)
    return records, reports


LA_DETAILS = {}
