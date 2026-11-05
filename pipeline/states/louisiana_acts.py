"""Reads Louisiana's appropriation acts and veto letters, then matches them to requests.

Louisiana has no form numbers in its acts, so matches depend on organization names.
A match is "confirmed" only when the full organization name appears in the entry.
Weaker evidence is labeled as possible, and it's never counted as funded.

Veto letters give exact printed page and line numbers ("Delete Lines 4 and 5 on Page
41 of 76"), so vetoes are applied by position. Every veto has to land on a parsed entry
or it's reported, because an unplaced veto could leave a vetoed item showing as funded.
"""
import json
import pathlib
import re
import site
import sys
from difflib import SequenceMatcher

sys.path.append(site.getusersitepackages())
import pymupdf  # noqa: E402

from fetch import cache_path  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
AMOUNT_X = 430
LINE_NUMBER_X = 90
AMOUNT = re.compile(r"^\(?\$?\(?\d{1,3}(?:,\d{3})*\)?$")
STOP_TOKENS = {"the", "inc", "incorporated", "llc", "corp", "corporation", "a", "of", "and", "for", "in", "co", "ltd", "l", "c"}
# These act types hold the money for NGO requests. Others in the session are not appropriations.
OPERATING_BILLS = ("HB1", "HB312")
CAPITAL_BILLS = ("HB2",)
BILL_LABEL = {"HB1": "Act 3 (HB 1, main budget, FY2026-27)", "HB2": "Act 4 (HB 2, capital outlay)", "HB312": "Act 961 (HB 312, supplemental, FY2025-26)"}


def document_path(acts, bill, kind="act"):
    return cache_path(acts[bill][kind]["url"], "louisiana_acts")


def page_rows(page):
    """Return [{line, text, amount, negative}] using the act's printed line numbers."""
    words = sorted(page.get_text("words"), key=lambda w: (w[1], w[0]))
    rows, current, anchor = [], [], None
    for word in words:
        if anchor is None or word[1] - anchor <= 4:
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
        if not (row[0][0] < LINE_NUMBER_X and row[0][4].isdigit()):
            continue
        line = int(row[0][4])
        text_words, amount, negative = [], None, False
        for word in row[1:]:
            token = word[4]
            if word[0] >= AMOUNT_X and AMOUNT.match(token):
                negative = "(" in token
                amount = int(re.sub(r"[^\d]", "", token))
            elif token not in ("$", "$(") or word[0] < AMOUNT_X:
                text_words.append(token)
        parsed.append({"line": line, "text": " ".join(text_words).strip(), "amount": amount, "negative": negative})
    return parsed


def read_pages(path):
    """Return [(printed_page, rows, full_text)] for pages that have a printed page number."""
    doc = pymupdf.open(path)
    pages = []
    for page in doc:
        text = page.get_text()
        printed = re.search(r"Page (\d+) of \d+", text)
        if printed:
            pages.append((int(printed.group(1)), page_rows(page), text))
    return pages


def parse_operating(path, bill):
    """Read "Payable out of ... to X for Y ... $ amount" entries from a budget act."""
    entries, open_entry = [], None
    for page_number, rows, _ in read_pages(path):
        for row in rows:
            if row["text"].startswith("Payable out of"):
                open_entry = {"bill": bill, "page": page_number, "start": row["line"], "end": row["line"], "parts": [], "span": [(page_number, row["line"])]}
            if open_entry is None:
                continue
            open_entry["parts"].append(row["text"])
            open_entry["span"].append((page_number, row["line"]))
            open_entry["end"] = row["line"]
            if row["amount"] is not None:
                open_entry["amount"] = row["amount"]
                open_entry["negative"] = row["negative"]
                open_entry["text"] = " ".join(open_entry.pop("parts"))
                entries.append(open_entry)
                open_entry = None
            elif len(open_entry["parts"]) > 14:
                open_entry = None  # A long passage without an amount is prose, not an entry.
    return entries


def parse_capital(path, bill):
    """Read capital outlay blocks: an entity header, then projects with funding rows."""
    entries, entity, project = [], None, None

    def close():
        nonlocal project
        if project and project["amounts"]:
            total = project.get("total") or sum(a for a, _ in project["amounts"])
            entries.append({"bill": bill, "page": project["page"], "start": project["start"], "end": project["end"],
                            "text": f"{entity}: {' '.join(project['parts'])}", "amount": total, "negative": False,
                            "span": project["span"], "funding_rows": project["amounts"]})
        project = None

    for page_number, rows, _ in read_pages(path):
        for row in rows:
            text = row["text"]
            header = re.match(r"^\d{2}/[A-Z0-9]{2,4}\s+(.+)$", text)
            if header:
                close()
                entity = header.group(1)
                continue
            number = re.match(r"^\((\d{5,7})\)\s*(.*)$", text)
            if number:
                close()
                project = {"page": page_number, "start": row["line"], "end": row["line"], "parts": [number.group(2)], "amounts": [], "span": [(page_number, row["line"])]}
                continue
            if project is None or entity is None:
                continue
            project["span"].append((page_number, row["line"]))
            project["end"] = row["line"]
            if text.startswith("Total") and row["amount"] is not None:
                project["total"] = row["amount"]
            elif row["amount"] is not None:
                project["amounts"].append((row["amount"], (page_number, row["line"])))
            elif not text.startswith(("Payable", "Priority")) and not project["amounts"]:
                project["parts"].append(text)
    close()
    return entries


def parse_veto_letter(path):
    """Return ({(page, line)}, ranges, veto_count). Layout varies, so match the location phrases."""
    text = "\n".join(page.get_text() for page in pymupdf.open(path))
    ranges = []
    for first, joiner, last, page in re.findall(r"(\d+)\s+(through|and)\s+(\d+)\s+on\s+Page\s+(\d+)\s+of", text):
        ranges.append((int(page), int(first), int(last)))
    for line, page in re.findall(r"Line\s+(\d+)\s+on\s+Page\s+(\d+)\s+of", text):
        ranges.append((int(page), int(line), int(line)))
    lines = {(page, n) for page, first, last in ranges for n in range(first, last + 1)}
    return lines, ranges, len(set(re.findall(r"Veto\s+No\.\s*(\d+)", text)))


def apply_vetoes(entries, vetoed_lines, ranges):
    """Mark entries by position and report any veto that doesn't land on an entry."""
    placed = set()
    for entry in entries:
        span = set(entry["span"])
        if "funding_rows" in entry:
            # A capital project can lose some of its funding lines and keep others.
            rows = entry["funding_rows"]
            lost = [amount for amount, where in rows if where in vetoed_lines]
            entry["vetoed_amount"] = sum(lost)
            entry["fully_vetoed"] = bool(rows) and len(lost) == len(rows)
            if lost and not entry["fully_vetoed"]:
                entry["amount"] = sum(a for a, w in rows if w not in vetoed_lines)
        else:
            entry["fully_vetoed"] = bool(span & vetoed_lines)
            entry["vetoed_amount"] = entry["amount"] if entry["fully_vetoed"] else 0
        for page, first, last in ranges:
            if any((page, n) in span for n in range(first, last + 1)):
                placed.add((page, first, last))
    return [r for r in ranges if r not in placed]


def normalize(text):
    text = re.sub(r"[^a-z0-9& ]", " ", (text or "").lower().replace("'", ""))
    return re.sub(r"\s+", " ", text).strip()


def significant(name):
    return [t for t in normalize(name).split() if t not in STOP_TOKENS]


def name_variants(request):
    """The legal and listed names, split at "dba" so either name can match."""
    names = set()
    for raw in (request.get("name"), request.get("listed_name")):
        for part in re.split(r"\bd/?b/?a\b|\bdoing business as\b", raw or "", flags=re.I):
            part = re.sub(r"[,.]?\s*\b(inc|llc|l\.l\.c|incorporated|corp|corporation)\b\.?\s*$", "", part.strip(), flags=re.I)
            part = re.sub(r"\(.*?\)", "", part).strip(" ,.")
            part = re.sub(r"^the\s+", "", part, flags=re.I)
            if part:
                names.add(normalize(part))
    return names


def recipient_of(entry_text):
    """Pull the recipient phrase out of "Payable ... to X for Y"."""
    text = re.sub(r"^Payable out of .*?(Fund|Revenues|Dedications)\b.*?(?=\bto\b|\bthe\b)", "", entry_text)
    match = re.match(r"\s*(?:to\s+)?(?:the\s+)?(.+?)(?:\s+for\s+|$)", text)
    return normalize(match.group(1)) if match else ""


def classify(request, entry):
    """Return "confirmed", "possible" or None for one request against one act entry."""
    if entry.get("negative"):
        return None
    haystack = " " + normalize(entry["text"]) + " "
    for variant in name_variants(request):
        tokens = variant.split()
        meaningful = [t for t in tokens if t not in STOP_TOKENS]
        if len(meaningful) >= 2 and len(variant) >= 8 and f" {variant} " in haystack:
            return "confirmed"
    recipient = recipient_of(entry["text"])
    for variant in name_variants(request):
        meaningful = [t for t in variant.split() if t not in STOP_TOKENS]
        if len(meaningful) >= 3 and set(meaningful) <= set(haystack.split()):
            return "possible"
        if recipient and len(meaningful) >= 2 and SequenceMatcher(None, variant, recipient).ratio() >= 0.88:
            return "possible"
    return None


def load_all_entries(session="26RS"):
    """Read every budget entry and apply vetoes. Returns (entries, report)."""
    acts = json.loads((ROOT / "data" / "derived" / f"la_acts_{session}.json").read_text(encoding="utf-8"))
    entries, report = [], {}
    for bill in OPERATING_BILLS + CAPITAL_BILLS:
        if bill not in acts or "act" not in acts[bill]:
            report[bill] = {"status": "missing"}
            continue
        path = document_path(acts, bill)
        if not path.exists():
            report[bill] = {"status": "missing"}
            continue
        parsed = parse_capital(path, bill) if bill in CAPITAL_BILLS else parse_operating(path, bill)
        unplaced, veto_count, range_count = [], 0, 0
        if "veto" in acts[bill] and document_path(acts, bill, "veto").exists():
            lines, ranges, veto_count = parse_veto_letter(document_path(acts, bill, "veto"))
            range_count = len(ranges)
            unplaced = apply_vetoes(parsed, lines, ranges)
        for entry in parsed:
            entry["act_url"] = acts[bill]["act"]["url"]
            entry["veto_url"] = acts[bill].get("veto", {}).get("url")
            entry.setdefault("fully_vetoed", False)
            entry.setdefault("vetoed_amount", 0)
        entries += parsed
        report[bill] = {"status": "read", "entries": len(parsed), "vetoes_in_letter": veto_count, "veto_ranges_read": range_count, "vetoes_not_placed": unplaced}
    return entries, report


def text_mentions(requests, matches, session="26RS"):
    """Find names in act text outside standard funding lines, such as conditions and notes."""
    acts = json.loads((ROOT / "data" / "derived" / f"la_acts_{session}.json").read_text(encoding="utf-8"))
    pages = []
    for bill in OPERATING_BILLS + CAPITAL_BILLS:
        if bill in acts and "act" in acts[bill] and document_path(acts, bill).exists():
            for number, rows, _ in read_pages(document_path(acts, bill)):
                pages.append((bill, number, " " + normalize(" ".join(r["text"] for r in rows)) + " "))
    mentions = {}
    for ngo_id, request in requests.items():
        if matches[ngo_id]["confirmed"] or matches[ngo_id]["possible"]:
            continue
        for variant in name_variants(request):
            meaningful = [t for t in variant.split() if t not in STOP_TOKENS]
            if len(meaningful) < 2 or len(variant) < 8:
                continue
            hits = [f"{BILL_LABEL[bill]} p. {number}" for bill, number, text in pages if f" {variant} " in text]
            if hits:
                mentions[ngo_id] = hits
                break
    return mentions


def match_requests(requests, entries):
    """Return {ngo_id: {"confirmed": [...], "possible": [...]}} at the organization level."""
    results = {}
    for ngo_id, request in requests.items():
        found = {"confirmed": [], "possible": []}
        for entry in entries:
            kind = classify(request, entry)
            if kind:
                found[kind].append(entry)
        results[ngo_id] = found
    return results
