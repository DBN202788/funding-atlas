"""Utah adapter: Legislator Requests for Appropriation.

The list shows who requested what and how much. It doesn't show whether a request was
funded, so every record says the outcome hasn't been checked. le.utah.gov can't be reached
from every machine, so pipeline/ut_fetch.py saves the list on GitHub and this reads that copy.
Each legislator's printed totals are checked against their rows before publishing.
"""
import html as html_lib
import re

from fetch import RAW
from tagging import categorize, recipient_type

SOURCE_FILE = RAW / "utah" / "requests_all.html"
LIST_URL = "https://le.utah.gov/sessions/requestsAppropSpon.jsp"
PROGRAM = "Legislator Request for Appropriation"
STATUS = "Requested (budget not yet checked)"


def money(text):
    """Some requests reduce a funding source, shown as -$1,000 or ($1,000)."""
    amount = int(re.sub(r"[^\d]", "", text) or 0)
    return -amount if "-" in text or "(" in text else amount


def clean(fragment):
    return re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def add_money(entry, sponsor, year, fund, one_time, ongoing):
    entry["funds"].add(fund)
    if sponsor not in entry["sponsors"]:
        entry["sponsors"].append(sponsor)
    # A request listed under several sponsors shows the same money each time, so count it once.
    row_key = (year, fund, one_time, ongoing)
    if row_key not in entry["by_sponsor"]:
        entry["by_sponsor"].add(row_key)
        entry["one_time"] += one_time
        entry["ongoing"] += ongoing


def parse_list(page):
    """Returns (requests, problems). A request can span funds and sponsors, so rows merge by issue."""
    start = page.find('class="approptbl"')
    if start < 0:
        return [], ["The request table wasn't found."]
    table = page[start:page.find("</table>", start)]
    requests, problems = {}, []
    sponsor, entry, one_time_sum, ongoing_sum, sponsors_seen = None, None, 0, 0, 0
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", table, flags=re.S | re.I):
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, flags=re.S | re.I)
        if len(cells) == 1:
            sponsor, one_time_sum, ongoing_sum = clean(cells[0]), 0, 0
            sponsors_seen += 1
        elif len(cells) == 3 and clean(cells[0]).startswith("Totals"):
            printed = (money(clean(cells[1])), money(clean(cells[2])))
            # The page's totals can round by a dollar.
            if abs(printed[0] - one_time_sum) > 1 or abs(printed[1] - ongoing_sum) > 1:
                problems.append(f"{sponsor}: rows add to {(one_time_sum, ongoing_sum)}, but the page prints {printed}.")
        elif len(cells) == 5 and "<th" not in row:
            year, fund, one_time, ongoing = clean(cells[1]), clean(cells[2]), money(clean(cells[3])), money(clean(cells[4]))
            one_time_sum += one_time
            ongoing_sum += ongoing
            if "<a" not in cells[0]:
                if entry is None:
                    problems.append(f"{sponsor}: a continuation row had no request above it.")
                    continue
                add_money(entry, sponsor, year, fund, one_time, ongoing)
                continue
            link = re.search(r'href="([^"]+)"[^>]*>(.*?)</a>', cells[0], flags=re.S)
            committee = re.search(r'committee\.jsp[^"]*"[^>]*>(.*?)</a>', cells[0], flags=re.S)
            dated = re.search(r"\(([A-Z][a-z]{2,8}\.? \d{1,2}, \d{4})\)\s*$", clean(cells[0]))
            url, title = link.group(1), clean(link.group(2))
            key = url if "/issues/" in url else f"{year}:{title}"
            entry = requests.setdefault(key, {"url": url, "title": title, "year": year, "committee": clean(committee.group(1)) if committee else None, "date": dated.group(1) if dated else None, "funds": set(), "sponsors": [], "by_sponsor": set(), "one_time": 0, "ongoing": 0})
            add_money(entry, sponsor, year, fund, one_time, ongoing)
    if not sponsors_seen:
        problems.append("No legislators were found in the table.")
    return list(requests.values()), problems


def split_title(title):
    """'H.A.R. 104 - Program name' becomes ('H.A.R. 104', 'Program name')."""
    match = re.match(r"^([A-Z](?:\.[A-Z])*\.?\s*\d+)\s*-\s*(.+)$", title)
    return (match.group(1), match.group(2)) if match else (None, title)


def build_records(requests):
    records, used = [], {}
    for request in requests:
        number, name = split_title(request["title"])
        issue = re.search(r"/issues/(\d+)", request["url"])
        base = f"UT-{request['year']}-{issue.group(1) if issue else re.sub(r'[^a-z0-9]+', '-', name.lower())[:50]}"
        used[base] = used.get(base, 0) + 1
        total = request["one_time"] + request["ongoing"]
        parts = [f"${request['one_time']:,} one-time"] if request["one_time"] else []
        if request["ongoing"]:
            parts.append(f"${request['ongoing']:,} ongoing")
        evidence = f"Utah's legislator request list shows {', '.join(parts) or 'no amount'} from {', '.join(sorted(request['funds']))}. The list doesn't show whether it was funded, so check the budget bills."
        records.append({
            "id": base if used[base] == 1 else f"{base}-{used[base]}",
            "state": "UT",
            "program": PROGRAM,
            "fiscal_year": f"{request['year']} General Session",
            "name": name,
            "organization": None,
            "group": request["committee"],
            "category": categorize(name),
            "recipient_type": recipient_type(name),
            "sponsor": "; ".join(request["sponsors"]) or None,
            "requested": total or None,
            "appropriated": None,
            "vetoed_amount": None,
            "status": STATUS,
            "form_number": number,
            "search_text": request["date"],
            "match_method": "Listed by the Legislature",
            "evidence": evidence,
            "source_url": request["url"] if request["url"].startswith("http") else LIST_URL,
            "budget_url": None,
            "veto_url": None,
        })
    return records


def collect(refresh=False):
    """Reads the saved copy. The fetch runs separately, on a machine that can reach the site."""
    if not SOURCE_FILE.exists():
        return [], [{"state": "UT", "fiscal_year": "All", "status": "unreachable", "source": LIST_URL, "reason": "Run the Fetch workflow with ut_fetch.py to save the list."}]
    requests, problems = parse_list(SOURCE_FILE.read_text(encoding="utf-8", errors="replace"))
    if problems or not requests:
        return [], [{"state": "UT", "fiscal_year": "All", "status": "held_back", "source": LIST_URL, "reason": "; ".join(problems[:5]) or "No requests were read."}]
    records = build_records(requests)
    reports = []
    for year in sorted({r["fiscal_year"] for r in records}, reverse=True):
        here = [r for r in records if r["fiscal_year"] == year]
        reports.append({"state": "UT", "fiscal_year": year, "status": "published", "source": LIST_URL, "records": len(here), "requested": sum(r["requested"] or 0 for r in here)})
    return records, reports
