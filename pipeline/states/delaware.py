"""Delaware Grant-in-Aid adapter.

Each year's Grant-in-Aid act lists recipients and amounts. The published PDF is
marked up: struck-through text is last year's wording or amount, and unstruck
text is what the act enacts. Reading the strike lines from the PDF drawing layer
is what lets us separate the two. Plain text extraction runs them together.

Every parsed section is reconciled against the totals printed in the act, and a
year that does not reconcile is reported and left out of the published data.
"""
import re
import site
import sys

sys.path.append(site.getusersitepackages())
import pymupdf  # noqa: E402

from fetch import fetch  # noqa: E402
from tagging import categorize, recipient_type  # noqa: E402

BASE = "https://legis.delaware.gov"
BILLS = [
    ("FY2027", "SB 337", BASE + "/BillDetail?LegislationId=143661"),
    ("FY2026", "HB 230", BASE + "/BillDetail?LegislationId=142646"),
    ("FY2025", "SB 327", BASE + "/BillDetail/141695"),
    ("FY2024", "HB 197", BASE + "/BillDetail?LegislationId=140762"),
    ("FY2023", "SB 252", BASE + "/BillDetail?LegislationId=129856"),
    ("FY2022", "HB 265", BASE + "/BillDetail?LegislationId=79013"),
    ("FY2021", "SB 260", BASE + "/BillDetail?LegislationId=48180"),
    ("FY2020", "HB 260", BASE + "/BillDetail?legislationId=47901"),
    ("FY2019", "SB 237", BASE + "/BillDetail?LegislationId=27026"),
    ("FY2018", "HB 281", BASE + "/BillDetail?legislationId=26140"),
    ("FY2017", "SB 295", BASE + "/BillDetail?LegislationId=24174"),
]
AMOUNT = re.compile(r"^\$?\d{1,3}(,\d{3})*$")
ACCOUNT_CODE = re.compile(r"\((\d{2}-\d{2}-\d{2})\)")
SECTION_TOTAL = re.compile(r"TOTAL\W*Section\s+(\d)", re.I)
ROW_TOLERANCE = 4.0
AMOUNT_COLUMN_X = 430  # Amounts in prose or page footers sit left of the money columns.
LAST_SECTION = 4


def find_act_pdf(detail_html):
    """Prefer the signed session law, falling back to the bill text."""
    law = re.search(r'(/SessionLaws/Chapter/GetPdfDocument\?fileAttachmentId=\d+)', detail_html)
    if law:
        return BASE + law.group(1), "Signed session law"
    bill = re.search(r'(/json/BillDetail/GetPdfDocument\?fileAttachmentId=\d+)', detail_html)
    if bill:
        return BASE + bill.group(1), "Bill text"
    return None, None


def horizontal_lines(page):
    """Return thin dark horizontal segments, which are strike and underline marks."""
    lines = []
    for drawing in page.get_drawings():
        rect = drawing["rect"]
        fill = drawing.get("fill")
        color = drawing.get("color")
        is_white = (fill == (1.0, 1.0, 1.0)) if fill else (color == (1.0, 1.0, 1.0))
        if rect.height <= 2.0 and rect.width >= 2.0 and not is_white:
            lines.append((rect.x0, rect.x1, (rect.y0 + rect.y1) / 2))
    return lines


def is_struck(word, lines):
    x0, y0, x1, y1 = word[:4]
    height = y1 - y0
    low, high = y0 + height * 0.30, y0 + height * 0.72
    covered = 0.0
    for lx0, lx1, ly in lines:
        if low <= ly <= high:
            covered += max(0.0, min(x1, lx1) - max(x0, lx0))
    return covered >= (x1 - x0) * 0.6


def bold_bands(page):
    bands = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                if "bold" in span["font"].lower() and span["text"].strip():
                    bands.append((span["bbox"][1], span["bbox"][3]))
    return bands


def page_rows(page):
    """Group a page's words into rows, recording struck words and bold text."""
    lines = horizontal_lines(page)
    bands = bold_bands(page)
    words = sorted(page.get_text("words"), key=lambda w: (w[1], w[0]))
    rows, current, anchor = [], [], None
    for word in words:
        if anchor is None or word[1] - anchor <= ROW_TOLERANCE:
            current.append(word)
            anchor = anchor if anchor is not None else word[1]
        else:
            rows.append(current)
            current, anchor = [word], word[1]
    if current:
        rows.append(current)

    parsed = []
    for row in rows:
        row = sorted(row, key=lambda w: w[0])
        y_mid = (row[0][1] + row[0][3]) / 2
        text_words, amounts = [], []
        for word in row:
            token = word[4]
            if word[0] < 72 and token.isdigit():
                continue  # bill line number
            if token == "$":
                continue
            if AMOUNT.match(token) and word[0] >= AMOUNT_COLUMN_X:
                if not is_struck(word, lines):
                    amounts.append((word[0], int(token.replace("$", "").replace(",", ""))))
                continue
            if not is_struck(word, lines):
                text_words.append(token)
        parsed.append({
            "text": " ".join(text_words).strip(),
            "amounts": amounts,
            "bold": any(low - 1 <= y_mid <= high + 1 for low, high in bands),
        })
    return parsed


def parse_act(pdf_bytes):
    """Return (items, printed_section_totals, grand_total)."""
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    items, printed, grand = [], {}, None
    section, heading, pending, started = None, None, "", False

    for page_number, page in enumerate(doc, start=1):
        for row in page_rows(page):
            text, amounts = row["text"], row["amounts"]

            # The grand total is printed in the synopsis on the last page.
            if text.upper().startswith("GRAND TOTAL") and amounts:
                grand = amounts[-1][1]
                continue

            start = re.match(r"Section\s+(\d+)\.", text)
            if start:
                number = int(start.group(1))
                if number <= LAST_SECTION and number == (section or 0) + 1:
                    section = number
                    heading, pending, started = None, "", True
                    continue
            if not started:
                continue

            if re.search(r"^TOTAL\b", text, re.I):
                match = SECTION_TOTAL.search(text)
                if match and amounts:
                    printed[int(match.group(1))] = amounts[-1][1]
                    if int(match.group(1)) == LAST_SECTION:
                        started = False  # Everything after this is prose conditions.
                pending = ""
                continue

            if not text and not amounts:
                pending = ""
                continue
            if not text or re.match(r"(Account Code|Category/Description|Organization/Description|Amount\b|Page \d+ of|CG:|Released:|\d{10})", text):
                continue

            if amounts:
                name = (pending + " " + text).strip() if pending else text
                pending = ""
                code = ACCOUNT_CODE.search(name)
                name = ACCOUNT_CODE.sub("", name).strip(" -")
                items.append({
                    "section": section,
                    "heading": heading,
                    "name": re.sub(r"\s+", " ", name),
                    "amount": amounts[-1][1],
                    "account_code": code.group(1) if code else None,
                    "page": page_number,
                })
            elif row["bold"] or ACCOUNT_CODE.match(text):
                heading = ACCOUNT_CODE.sub("", text).strip()
                pending = ""
            else:
                pending = (pending + " " + text).strip()
    return items, printed, grand


def reconcile(items, printed, grand):
    """Compare parsed sums with the totals printed in the act."""
    report = {"sections": {}, "grand_printed": grand, "ok": True}
    for number, total in sorted(printed.items()):
        parsed = sum(i["amount"] for i in items if i["section"] == number)
        report["sections"][number] = {"printed": total, "parsed": parsed, "difference": parsed - total}
        if parsed != total:
            report["ok"] = False
    parsed_all = sum(i["amount"] for i in items)
    report["grand_parsed"] = parsed_all
    # Some acts print no grand total, so the section totals have to cover all four sections.
    if len(printed) != LAST_SECTION or (grand is not None and parsed_all != grand):
        report["ok"] = False
    return report


SECTION_NAMES = {
    1: "Government Units & Senior Centers",
    2: "One-Times & Community Agencies",
    3: "Fire Companies & Ambulance",
    4: "Veterans Organizations",
}
HEADING_CATEGORY = {
    "aging": "Seniors & Veterans",
    "arts, historical, or recreation": "Arts, Culture & History",
    "economic, housing, or labor services": "Workforce & Economic Development",
    "family and youth services": "Youth & Education",
    "health or disability services": "Health & Disability",
    "neighborhood and community services": "Housing & Human Services",
}


SENIOR_CENTER_HEADINGS = {"new castle county", "kent county", "sussex county", "city of wilmington"}


def clean_name(name, last_recipient):
    """Turn prose-style lines from the veterans and fire sections into readable names."""
    named = re.search(r"appropriated to (?:the )?(.+?)(?: the following| for |,? to be used)", name)
    if named:
        return named.group(1).strip(" ,")
    purpose = re.match(r"\([ivx]+\)\s*(?:the\s+)?(.+)", name)
    if purpose:
        return f"{last_recipient}: {purpose.group(1).rstrip('.')}"[:140]
    return name


def discover_bills(refresh=False):
    """Add Grant-in-Aid bills listed on the Controller General's page that aren't known yet."""
    bills = list(BILLS)
    known = {year for year, _, _ in bills}
    try:
        page = fetch(BASE + "/Offices/ControllerGeneral/AppropriationBills", "delaware", refresh=refresh).decode("utf-8", "replace")
    except Exception:
        return bills
    pattern = r'href="([^"]*BillDetail[^"]*)"[^>]*>\s*([A-Z]{2}\s*\d+)\s*-\s*FY\s*(\d{2})\s+(?:Grants?[- ]in[- ]Aid|GIA)'
    for href, bill, year in re.findall(pattern, page, re.I):
        fiscal_year = f"FY20{year}"
        if fiscal_year not in known:
            known.add(fiscal_year)
            bills.insert(0, (fiscal_year, re.sub(r"\s+", " ", bill), href.replace("http://", "https://")))
    return bills


def collect(refresh=False):
    """Return (records, source_reports) for every year that reconciles.

    A refresh rechecks the two newest bills, because a bill's signed law can be
    attached to its page weeks after the bill first appears.
    """
    records, reports = [], []
    bills = discover_bills(refresh)
    newest = {year for year, _, _ in sorted(bills, reverse=True)[:2]}
    for fiscal_year, bill, detail_url in bills:
        report = {"state": "DE", "fiscal_year": fiscal_year, "source": detail_url, "bill": bill}
        try:
            html = fetch(detail_url, "delaware", refresh=refresh and fiscal_year in newest).decode("utf-8", "replace")
            pdf_url, label = find_act_pdf(html)
            if not pdf_url:
                report.update(status="skipped", reason="No act PDF link found")
                reports.append(report)
                continue
            items, printed, grand = parse_act(fetch(pdf_url, "delaware"))
        except Exception as error:
            report.update(status="error", reason=str(error))
            reports.append(report)
            continue

        check = reconcile(items, printed, grand)
        report.update(status="published" if check["ok"] else "held_back", reconcile=check, records=len(items), document=label, pdf=pdf_url)
        reports.append(report)
        if not check["ok"]:
            continue

        last_recipient = ""
        for index, item in enumerate(items):
            item["name"] = clean_name(item["name"], last_recipient)
            if not item["name"].startswith(last_recipient + ":") or not last_recipient:
                last_recipient = item["name"]
            heading = (item["heading"] or "").strip()
            category = HEADING_CATEGORY.get(heading.lower())
            if not category:
                if item["section"] == 3:
                    category = "Public Safety & Justice"
                elif item["section"] == 4 or (item["section"] == 1 and heading.lower() in SENIOR_CENTER_HEADINGS):
                    category = "Seniors & Veterans"
                else:
                    category = categorize(item["name"], heading)
            kind = "State program or account" if item["account_code"] else recipient_type(item["name"])
            if item["section"] == 3:
                kind = "Fire or ambulance company"
            elif item["section"] == 4:
                kind = "Likely nonprofit (auto-tagged)"
            records.append({
                "id": f"DE-{fiscal_year}-{index:04d}",
                "state": "DE",
                "program": "Grant-in-Aid",
                "fiscal_year": fiscal_year,
                "name": item["name"],
                "group": SECTION_NAMES.get(item["section"], "") + (f" / {heading}" if heading else ""),
                "category": category,
                "recipient_type": kind,
                "sponsor": None,
                "requested": None,
                "appropriated": item["amount"],
                "status": "Appropriated",
                "evidence": f"{bill} ({label}), p. {item['page']}; section totals reconciled",
                "source_url": pdf_url,
            })
    return records, reports
