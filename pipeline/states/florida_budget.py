"""Links Florida Senate request forms to the enacted budget and the veto message.

Since at least FY2021-22 the General Appropriations Act cites the Senate form
number beside each project, as "(SF 1994)" or "(Senate Form 1994)". That makes
these links exact, so no name matching is involved in deciding a project was funded.

The enrolled budget is the version sent to the Governor, so it still contains
items the Governor later vetoed. Vetoes are applied only for years where a veto
message has been processed, and other years say that the check is outstanding.
"""
import datetime
import json
import pathlib
import re
import site
import sys

sys.path.append(site.getusersitepackages())
import pymupdf  # noqa: E402

from fetch import cache_path, fetch  # noqa: E402

B = "https://www.flsenate.gov/Session/Bill"
BUDGETS = {
    "FY2026-27": ("HB 5001-E", f"{B}/2026E/5001E/BillText/er/PDF"),
    "FY2025-26": ("SB 2500", f"{B}/2025/2500/BillText/er/PDF"),
    "FY2024-25": ("HB 5001", f"{B}/2024/5001/BillText/er/PDF"),
    "FY2023-24": ("SB 2500", f"{B}/2023/2500/BillText/er/PDF"),
    "FY2022-23": ("HB 5001", f"{B}/2022/5001/BillText/er/PDF"),
    "FY2021-22": ("SB 2500", f"{B}/2021/2500/BillText/er/PDF"),
    "FY2020-21": ("HB 5001", f"{B}/2020/5001/BillText/er/PDF"),
    "FY2019-20": ("SB 2500", f"{B}/2019/2500/BillText/er/PDF"),
    "FY2018-19": ("HB 5001", f"{B}/2018/5001/BillText/er/PDF"),
    "FY2017-18": ("SB 2500", f"{B}/2017/2500/BillText/er/PDF"),
}
ROOT = pathlib.Path(__file__).resolve().parents[2]
VETO_OCR = {"FY2026-27": ROOT / "data" / "manual" / "fl_fy2026-27_veto_ocr.txt"}
VETO_SOURCE = {"FY2026-27": "https://static-s3.lobbytools.com/docs/2026/6/29/139263_2026_budget_veto_message.pdf"}

CITATION = re.compile(r"\(\s*(SF|Senate\s+Form|HF|HB)\s+(\d{3,4})\s*\)")
# Budget amounts always carry commas. A bare number after a citation is the next
# specific appropriation number, as in "(SF 3349). 2018 SPECIAL CATEGORIES".
AMOUNT_AFTER = re.compile(r"\s*\.*\s*(\d{1,3}(?:,\d{3})+)\b")
STOP_WORDS = {"the", "of", "and", "for", "in", "to", "a", "at", "on", "program", "project"}


DISCOVERED = ROOT / "data" / "derived" / "fl_budgets_discovered.json"


def known_budgets():
    budgets = dict(BUDGETS)
    if DISCOVERED.exists():
        budgets.update({k: tuple(v) for k, v in json.loads(DISCOVERED.read_text(encoding="utf-8")).items()})
    return budgets


def discover_budget(fiscal_year):
    """Try the usual addresses for a new year's enrolled budget. Returns (bill, url) or None.

    A candidate counts only if it's a PDF that cites many Senate forms, which rules
    out other bills that happen to live at a similar address.
    """
    year = fiscal_year[2:6]
    sessions = [year, f"{year}E", f"{year}A", f"{year}B", f"{year}C"]
    numbers = [("HB 5001", "5001"), ("SB 2500", "2500")]
    for session in sessions:
        suffix = session[len(year):]
        for bill, number in numbers:
            url = f"{B}/{session}/{number}{suffix}/BillText/er/PDF"
            try:
                body = fetch(url, "florida_gaa")
            except Exception:
                continue
            if body[:4] != b"%PDF":
                cache_path(url, "florida_gaa").unlink(missing_ok=True)
                continue
            items, _ = parse_budget(body)
            if len(items) >= 100:
                found = json.loads(DISCOVERED.read_text(encoding="utf-8")) if DISCOVERED.exists() else {}
                found[fiscal_year] = [f"{bill}{'-' + suffix if suffix else ''}", url]
                DISCOVERED.parent.mkdir(parents=True, exist_ok=True)
                DISCOVERED.write_text(json.dumps(found, indent=1), encoding="utf-8")
                return found[fiscal_year][0], url
    return None


def tokens(text):
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if t not in STOP_WORDS}


def title_overlap(title, context):
    wanted = tokens(title)
    return len(wanted & tokens(context)) / len(wanted) if wanted else 0.0


def budget_line_text(context):
    """Return the project wording just before a citation, without the previous line item."""
    boundaries = list(re.finditer(r"\.{3,}\s*[\d,]*|\d,\d{3}\s*\n|\)\s*\n", context))
    tail = context[boundaries[-1].end():] if boundaries else context
    return re.sub(r"\s+", " ", tail).strip()


def squeeze(text):
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def parse_budget(pdf_bytes):
    """Return {form_number: item} from the citations in a budget PDF."""
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    items = {}
    uncited_amounts = 0
    for page_number, page in enumerate(doc, start=1):
        text = page.get_text()
        matches = list(CITATION.finditer(text))
        index = 0
        while index < len(matches):
            # Adjacent citations such as "(HF 2115) (SF 1994)" describe a single project.
            cluster = [matches[index]]
            while index + 1 < len(matches) and not text[cluster[-1].end():matches[index + 1].start()].strip():
                index += 1
                cluster.append(matches[index])
            index += 1

            senate = [m.group(2) for m in cluster if m.group(1).startswith(("SF", "Senate"))]
            house = [m.group(2) for m in cluster if m.group(1).startswith(("HF", "HB"))]
            if not senate:
                continue
            after = AMOUNT_AFTER.match(text[cluster[-1].end():cluster[-1].end() + 160])
            amount = int(after.group(1).replace(",", "")) if after else None
            if amount is None:
                uncited_amounts += 1
            context = text[max(0, cluster[0].start() - 220):cluster[0].start()]
            for position, form in enumerate(senate):
                item = items.setdefault(form, {"amount": 0, "pages": [], "house": set(), "context": "", "has_amount": False, "line_text": "", "line_amounts": []})
                if not item["line_text"]:
                    item["line_text"] = budget_line_text(context)
                # One line can cite two forms, so its money is counted once, on the first form.
                if amount is not None and position == 0:
                    item["amount"] += amount
                    item["has_amount"] = True
                    item["line_amounts"].append(amount)
                if page_number not in item["pages"]:
                    item["pages"].append(page_number)
                item["house"].update(house)
                item["context"] += " " + context
    return items, uncited_amounts


def parse_vetoes(ocr_text):
    """Return vetoed form numbers read from OCR text, with nearby wording for checking."""
    senate, house = {}, {}
    for match in re.finditer(r"\b(SF|HF)\s*\)?\s*(\d{4})", ocr_text):
        context = ocr_text[max(0, match.start() - 240):match.start()]
        (senate if match.group(1) == "SF" else house).setdefault(match.group(2), []).append(context)
    squeezed = re.sub(r"[^a-z0-9]", "", ocr_text.lower())
    return senate, house, squeezed


def link(records, look_for_new_budgets=False):
    """Set budget status on Florida records in place and return per-year reports."""
    by_year = {}
    for record in records:
        by_year.setdefault(record["fiscal_year"], []).append(record)

    budgets = known_budgets()
    today = datetime.date.today()
    reports = []
    for fiscal_year, year_records in sorted(by_year.items()):
        # The budget for a fiscal year passes in the spring or summer of its first calendar year.
        session_over = (today.year, today.month) >= (int(fiscal_year[2:6]), 3)
        if fiscal_year not in budgets and look_for_new_budgets and session_over:
            found = discover_budget(fiscal_year)
            if found:
                budgets[fiscal_year] = found
        if fiscal_year not in budgets:
            for record in year_records:
                record["status"] = "Requested (budget not yet checked)"
            reports.append({"state": "FL", "fiscal_year": fiscal_year, "status": "requests_only",
                            "reason": "Budget link for this year has not been processed"})
            continue

        bill, url = budgets[fiscal_year]
        report = {"state": "FL", "fiscal_year": fiscal_year, "bill": bill, "source": url}
        try:
            items, without_amount = parse_budget(fetch(url, "florida_gaa"))
        except Exception as error:
            report.update(status="error", reason=str(error))
            reports.append(report)
            for record in year_records:
                record["status"] = "Requested (budget not yet checked)"
            continue

        veto_data = None
        if fiscal_year in VETO_OCR and VETO_OCR[fiscal_year].exists():
            veto_data = parse_vetoes(VETO_OCR[fiscal_year].read_text(encoding="utf-8"))

        forms = {r["form_number"]: r for r in year_records}
        overlaps, weak_links, vetoed_count, vetoed_amount, vetoed_by_title, funded, uncertain = [], [], 0, 0, 0, 0, 0
        for form, item in items.items():
            record = forms.get(form)
            if not record:
                continue
            overlap = title_overlap(record["name"], item["context"])
            overlaps.append(overlap)
            if overlap < 0.34:
                weak_links.append({"form": form, "title": record["name"], "budget_text": re.sub(r"\s+", " ", item["context"])[-140:]})

            record["budget_pages"] = item["pages"]
            record["match_method"] = "Exact form-number citation"
            record["appropriated"] = item["amount"] if item["has_amount"] else None
            pages = ", ".join(str(p) for p in item["pages"][:4])
            record["evidence"] = f"Request form {form} is cited in {bill} (enrolled), p. {pages}"
            record["budget_url"] = url

            if veto_data is None:
                record["status"] = "In enrolled budget (veto check pending)"
                funded += 1
                continue

            senate, house, squeezed = veto_data
            senate_contexts = senate.get(form, [])
            house_contexts = [c for h in item["house"] for c in house.get(h, [])]
            contexts = senate_contexts + house_contexts
            line_text = item["line_text"]
            record["budget_text"] = line_text

            # The veto message reuses the budget's wording, which can differ from the request form.
            # Scanned text can misread a digit, so a lone number hit needs matching wording too.
            both_numbers = bool(senate_contexts and house_contexts)
            wording = any(max(title_overlap(record["name"], c), title_overlap(line_text, c)) >= 0.4 for c in contexts)
            by_title = any(len(squeeze(t)) >= 18 and squeeze(t) in squeezed for t in (record["name"], line_text))
            # A project can be split across budget lines, and the Governor can veto only some of them.
            lines = len(item["line_amounts"])
            hits = max(len(senate_contexts), len(house_contexts))
            if lines > 1 and hits < lines and (both_numbers or (contexts and wording) or by_title):
                record["status"] = "Funded (veto check uncertain)"
                record["evidence"] += f"; this project has {lines} budget lines and the scanned veto message lists {hits}, so it may be partly vetoed. Check the source"
                record["veto_url"] = VETO_SOURCE.get(fiscal_year)
                uncertain += 1
            elif both_numbers or (contexts and wording) or by_title:
                method = "form numbers" if both_numbers else ("form number and wording" if contexts and wording else "wording")
                record["status"] = "Vetoed by Governor"
                record["vetoed_amount"] = record["appropriated"]
                record["appropriated"] = 0
                record["evidence"] += f"; listed in the Governor's veto message ({method} matched in scanned text)"
                record["veto_url"] = VETO_SOURCE.get(fiscal_year)
                vetoed_count += 1
                vetoed_by_title += 0 if contexts else 1
                vetoed_amount += record.get("vetoed_amount") or 0
            elif contexts:
                record["status"] = "Funded (veto check uncertain)"
                record["evidence"] += "; a form number in the scanned veto message matched but the wording did not, so check the source"
                record["veto_url"] = VETO_SOURCE.get(fiscal_year)
                uncertain += 1
            else:
                record["status"] = "Funded"
                funded += 1

        for record in year_records:
            if record["status"] == "Requested":
                record["status"] = "No budget citation found"

        overlaps.sort()
        report.update(
            status="published",
            forms_cited=len(items),
            forms_linked=len(overlaps),
            forms_cited_not_in_listing=sorted(set(items) - set(forms))[:40],
            citations_without_amount=without_amount,
            median_title_overlap=round(overlaps[len(overlaps) // 2], 2) if overlaps else None,
            weak_title_links=len(weak_links),
            weak_examples=weak_links[:8],
            funded_or_pending=funded,
            veto_uncertain=uncertain,
            vetoed=vetoed_count,
            vetoed_by_title_only=vetoed_by_title,
            vetoed_amount=vetoed_amount,
            veto_check="applied" if veto_data else "pending",
        )
        reports.append(report)
    return reports
