"""Florida adapter: Senate Local Funding Initiative Requests.

The Senate publishes one page per fiscal year with a sortable table of request
forms. Each row links to the full request form PDF. These are requests only, so
a record here says nothing about whether the project was funded until it is
linked to the General Appropriations Act.
"""
import datetime
import html as html_lib
import re

from fetch import cache_path, fetch
from tagging import categorize, recipient_type

BASE = "https://www.flsenate.gov"
FIRST_YEAR = 2017


def fiscal_years(today=None):
    """Every year from the first published through next year's, which may not exist yet."""
    today = today or datetime.date.today()
    return [f"FY{y}-{str(y + 1)[2:]}" for y in range(FIRST_YEAR, today.year + 2)]



ROW = re.compile(
    r'<tr>\s*<td class="centertext">\s*(?P<form>[^<]*?)\s*</td>\s*'
    r'<td>\s*<a href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>.*?</td>\s*'
    r'<td class="centertext">\s*(?P<sponsor>[^<]*?)\s*</td>\s*'
    r'<td class="righttext">(?:<span[^>]*>[^<]*</span>)?\s*(?P<amount>[^<]*?)\s*</td>\s*'
    r'<td>\s*(?P<committee>.*?)\s*</td>\s*</tr>',
    re.S,
)
COMMITTEE_CATEGORY = {
    "criminal and civil justice": "Public Safety & Justice",
}


def clean(text):
    text = html_lib.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    return re.sub(r"\s+", " ", text).strip()


def parse_listing(page_html, fiscal_year):
    rows = []
    for match in ROW.finditer(page_html):
        amount_text = re.sub(r"[^\d]", "", match.group("amount"))
        committee = clean(match.group("committee")).replace("Appropriations Subcommittee on ", "")
        title = clean(match.group("title"))
        form = clean(match.group("form"))
        category = categorize(title)
        if category == "Other":
            category = COMMITTEE_CATEGORY.get(committee.lower(), categorize(committee))
        href = match.group("href").replace("\\", "/")  # Older years use backslashes.
        rows.append({
            "id": f"FL-{fiscal_year}-{form}",
            "state": "FL",
            "program": "Senate Local Funding Initiative Request",
            "fiscal_year": "FY" + fiscal_year[2:6] + "-" + fiscal_year[7:],
            "name": title,
            "group": committee,
            "category": category,
            "recipient_type": recipient_type(title),
            "sponsor": clean(match.group("sponsor")) or None,
            "requested": int(amount_text) if amount_text else None,
            "appropriated": None,
            "status": "Requested",
            "form_number": form,
            "evidence": f"Florida Senate request form {form}, {fiscal_year}",
            "source_url": BASE + href if href.startswith("/") else href,
        })
    return rows


def collect(refresh=False):
    """Read every year. A refresh re-downloads only recent years, since older lists don't change."""
    records, reports = [], []
    years = fiscal_years()
    recent = set(years[-3:])
    for fiscal_year in years:
        url = f"{BASE}/Session/Appropriations/{fiscal_year}/LocalFundingRequests"
        report = {"state": "FL", "fiscal_year": fiscal_year, "source": url}
        is_next_year = fiscal_year == years[-1]
        if is_next_year and not refresh and not cache_path(url, "florida").exists():
            continue
        try:
            page = fetch(url, "florida", refresh=refresh and fiscal_year in recent).decode("utf-8", "replace")
        except Exception as error:
            if is_next_year:
                continue  # Next year's page normally doesn't exist yet.
            report.update(status="error", reason=str(error))
            reports.append(report)
            continue
        table_rows = len(re.findall(r"<tr>\s*<td", page))
        if table_rows == 0 and is_next_year:
            continue
        rows = parse_listing(page, fiscal_year)
        complete = len(rows) == table_rows and table_rows > 0
        report.update(status="published" if complete else "held_back", records=len(rows), table_rows=table_rows)
        reports.append(report)
        if complete:
            records.extend(rows)
    return records, reports
