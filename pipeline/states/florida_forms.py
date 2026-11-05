"""Reads program details from downloaded Florida request form PDFs.

Contact sections (questions 17 and 18d-f, and the lobbyist's details in 19) are
never read into the output. Only the recipient organization, its county and type,
and whether a lobbyist is listed are kept. Emails and phone numbers are also
scrubbed from free text, because applicants sometimes type them there.

Checkbox answers are small images in these PDFs, so they are read by finding the
option whose image differs from the others in its group.
"""
import hashlib
import json
import pathlib
import re
import site
import sys

sys.path.append(site.getusersitepackages())
import pymupdf  # noqa: E402

from fetch import cache_path  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
DERIVED = ROOT / "data" / "derived"
HEADER = re.compile(r"^\s*(The Florida Senate|Local Funding Initiative Request|Fiscal Year \d{4}-\d{4}.*|Page \d+ of \d+)\s*$")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE = re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?!\d)")
ORG_TYPES = [("For Profit", "For-profit"), ("501(c)(3)", "Nonprofit 501(c)(3)"), ("501(c)(4)", "Nonprofit 501(c)(4)"), ("Local Entity", "Local government or entity"), ("University or College", "University or college"), ("Other", "Other")]
SERVED_WORDS = r"(?:individual|people|person|resident|student|youth|child|adult|famil|citizen|participant|client|senior|veteran|household|patient|customer|member|visitor|learner|visits?)"


def scrub(text):
    return PHONE.sub("[phone removed]", EMAIL.sub("[email removed]", text or ""))


def tidy(text):
    return re.sub(r"\s+", " ", scrub(text or "")).strip()


def full_text(doc):
    lines = []
    for page in doc:
        for line in page.get_text("text", sort=True).splitlines():
            if not HEADER.match(line):
                lines.append(line)
    return "\n".join(lines)


def between(text, start, end):
    begin = re.search(start, text)
    if not begin:
        return ""
    stop = re.search(end, text[begin.end():])
    return text[begin.end():begin.end() + stop.start()] if stop else text[begin.end():]


def number(pattern, text):
    match = re.search(pattern + r"\s+\$?([\d,]+)", text)
    return int(match.group(1).replace(",", "")) if match and match.group(1).replace(",", "") else 0


def yes_no(pattern, text):
    match = re.search(pattern + r"\s*(Yes|No)\b", text)
    return match.group(1) if match else None


def checkbox_groups(doc):
    """Return [(label_text, page, y, image_digest)] for lines that have a checkbox image beside them."""
    options = []
    for page_number, page in enumerate(doc):
        images = [i for i in page.get_image_info(hashes=True) if i["bbox"][2] - i["bbox"][0] < 20]
        lines = []
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                text = "".join(span["text"] for span in line["spans"]).strip()
                if text:
                    lines.append((text, line["bbox"]))
        for image in images:
            x0, y0, x1, y1 = image["bbox"]
            mid = (y0 + y1) / 2
            beside = [t for t, box in lines if box[0] >= x0 and box[1] <= mid <= box[3] and box[0] - x1 < 40]
            if beside:
                options.append((beside[0], page_number, y0, image["digest"].hex()))
    return sorted(options, key=lambda o: (o[1], o[2]))


def selected(options):
    """The chosen option is the one whose image differs from the rest of its group."""
    if len(options) < 2:
        return None
    digests = [o[3] for o in options]
    rare = [o for o in options if digests.count(o[3]) == 1]
    return rare[0][0] if len(rare) == 1 else None


def group_after(options, first_label, count):
    """Find a run of options starting at a given label, skipping earlier matches already used."""
    for index, option in enumerate(options):
        if option[0].startswith(first_label):
            return index, options[index:index + count]
    return None, []


def parse_form(pdf_bytes):
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    text = full_text(doc)
    q6 = between(text, r"6\. Amount of the Nonrecurring", r"7\. Total Project Cost")
    q7 = between(text, r"7\. Total Project Cost", r"8\. Has this project")
    q13 = between(text, r"13\. Program Performance", r"14\. Is this project")
    q19 = between(text, r"19\. Lobbyist Contact Information", r"\Z")
    q18 = between(text, r"18\. Recipient Contact Information", r"19\. Lobbyist")

    purpose = between(q13, r"a\. What specific purpose or goal.*?\n", r"\n\s*b\. What activities")
    activities = between(q13, r"b\. What activities and services.*?\n", r"\n\s*c\. What direct services")
    direct = between(q13, r"c\. What direct services.*?\n", r"\n\s*d\. Who is the target")
    population = between(q13, r"d\. Who is the target population.*?\n", r"\n\s*e\. What is the expected")
    outcome = between(q13, r"e\. What is the expected benefit.*?(?:\n.*?measured\?)?\s*\n", r"\n\s*f\. What are the suggested")

    org = re.search(r"a\. Organization\s+(.+)", q18)
    county = re.search(r"b\. Municipality and County\s+(.+)", q18)

    options = checkbox_groups(doc)
    org_type = None
    index, group = group_after(options, "For Profit", 6)
    if group:
        chosen = selected(group)
        for key, label in ORG_TYPES:
            if chosen and key in chosen:
                org_type = label
                break
    first, federal_group = group_after(options, "Yes, Applied", 4)
    federal = selected(federal_group)
    state_group = options[first + 4:first + 8] if first is not None else []
    state_other = selected(state_group)

    operating, capital = number(r"Operating", q6), number(r"Fixed Capital Outlay", q6)
    state_ask = number(r"Total State Funds Requested", q6) or operating + capital
    match = {k: number(p, q7) for k, p in (("federal", r"Federal"), ("state", r"State \(excluding.*?\)"), ("local", r"Local"), ("other", r"Other"))}
    total_cost = number(r"Total Project Costs? for Fiscal Year \d{4}-\d{4}", q7)
    match_total = sum(match.values())

    lobbyist = re.search(r"a\. Name[ \t]+(\S.*)", q19)
    population_text, outcome_text = tidy(population), tidy(outcome)
    return {
        "organization": tidy(org.group(1)) if org else None,
        "county": tidy(county.group(1)) if county else None,
        "org_type": org_type,
        "agency": tidy(between(text, r"5\. State Agency to receive requested funds", r"State Agency contacted")) or None,
        "agency_contacted": yes_no(r"State Agency contacted\?", text),
        "operating": operating,
        "capital": capital,
        "state_request": state_ask,
        "match_total": match_total,
        "match_local": match["local"],
        "match_other": match["other"],
        "match_federal": match["federal"],
        "total_cost": total_cost,
        "prior_state_funding": yes_no(r"8\. Has this project previously received state funding\?", text),
        "future_funding": yes_no(r"9\. Is future-year funding likely to be requested\?", text),
        "shovel_ready": yes_no(r'"shovel ready" \(i\.e permitted\)\?', text),
        "federal_assistance": federal,
        "other_state_assistance": state_other,
        "has_lobbyist": bool(lobbyist and not lobbyist.group(1).strip().startswith("b. Firm")),
        "states_number_served": bool(re.search(r"\b\d[\d,]*\+?\s+(?:\w+\s+){0,3}?" + SERVED_WORDS, population_text, re.I)),
        "numeric_outcome_target": bool(re.search(r"\d+(?:\.\d+)?\s?(?:%|percent)", outcome_text, re.I)),
        "names_measurement_method": bool(re.search(r"\b(survey|pre[- /]?(?:and )?post|assessment|tracked?|tracking|data collection|report(?:s|ing)?|metric|evaluat|measured? (?:by|through|using))\b", outcome_text, re.I)),
        "description": tidy(between(text, r"4\. Project/Program Description", r"5\. State Agency"))[:900],
        "purpose": tidy(purpose)[:500],
        "activities": tidy(activities)[:500],
        "direct_services": tidy(direct)[:400],
        "population": population_text[:500],
        "outcome": outcome_text[:700],
    }


def extract_year(fiscal_year, records, refresh=False):
    """Parse every downloaded form for a year and keep results in data/derived.

    The results file is what gets committed, so the automatic update job can use
    past forms without holding the original PDFs and their contact details.
    """
    DERIVED.mkdir(parents=True, exist_ok=True)
    path = DERIVED / f"fl_forms_{fiscal_year}.json"
    saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    errors = []
    for record in records:
        form = record["form_number"]
        if form in saved and not refresh:
            continue
        pdf = cache_path(record["source_url"], "florida_forms")
        if not pdf.exists():
            continue
        try:
            saved[form] = parse_form(pdf.read_bytes())
        except Exception as error:
            errors.append((form, str(error)))
    path.write_text(json.dumps(saved, ensure_ascii=False, indent=0), encoding="utf-8")
    return saved, errors


def digest_of(path):
    return hashlib.sha1(pathlib.Path(path).read_bytes()).hexdigest()
