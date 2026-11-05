"""One-time look at Louisiana's page layout, run on GitHub's servers.

Writes a masked outline to the job summary so a parser can be written. Personal
details are hidden: a request page holds people's names and addresses, so field
values are shown only for fields that can't be personal. Budget bill pages and acts
are public government documents and are saved to the repository for parsing.
"""
import html as html_lib
import os
import pathlib
import re
import sys
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from fetch import USER_AGENT, cache_path, fetch  # noqa: E402

BASE = "https://www.legis.la.gov/legis"
SAMPLE_IDS = [3407, 3430, 3354]
BILLS = ["HB1", "HB2", "HB312", "HB313", "HB311", "HB310", "HB314", "SB1", "HB3"]
SAFE_FIELD = re.compile(r"(amount|funding|request|purpose|descri|project|budget|total|session|type|benefit|outcome|goal|title|parish)", re.I)
PERSONAL_FIELD = re.compile(r"(board|officer|address|director|ceo|incorporat|personnel|contact|email|phone|tax|ein|ssn|name|salary|member|city|zip)", re.I)
out = []


def masked_outline(page):
    page = re.sub(r"<script.*?</script>|<style.*?</style>|<!--.*?-->", " ", page, flags=re.S)
    start = page.find("PageBody")
    page = page[start - 200:] if start > 200 else page
    rows = []
    for match in re.finditer(r"<(span|td|th|a|h\d|label|input|textarea|div)\b([^>]*)>([^<]*)", page):
        tag, attrs, text = match.groups()
        element_id = re.search(r'\bid="([^"]+)"', attrs)
        href = re.search(r'href="([^"]+)"', attrs)
        element_id = element_id.group(1).replace("ctl00_PageBody_", "") if element_id else ""
        text = re.sub(r"\s+", " ", html_lib.unescape(text)).strip()
        if not text and not element_id and not href:
            continue
        if element_id and SAFE_FIELD.search(element_id) and not PERSONAL_FIELD.search(element_id):
            shown = text[:70]
        elif text.endswith(":") or text.endswith("?") or (text and len(text) < 45 and not re.search(r"\d", text) and not element_id and tag in ("th", "h1", "h2", "h3", "h4", "label")):
            shown = text[:90]  # A question or label, not an answer.
        else:
            shown = f"[hidden, {len(text)} chars]" if text else ""
        link = ""
        if href and re.search(r"ViewDocument|\.pdf|BillInfo", href.group(1), re.I):
            link = " href=" + html_lib.unescape(href.group(1))[:70]
        rows.append(f"{tag} id={element_id or '-'}{link} :: {shown}")
    return rows


def main():
    try:
        request = urllib.request.Request("https://www.legis.la.gov/robots.txt", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=30) as response:
            out.append("## robots.txt\n```\n" + response.read().decode("utf-8", "replace")[:800] + "\n```")
    except Exception as error:
        out.append(f"## robots.txt\nNot available: {error}")

    for ngo_id in SAMPLE_IDS:
        url = f"{BASE}/NGO/NgoDoc.aspx?NgoId={ngo_id}&search=ALL"
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:  # Deliberately not saved to disk.
                body = response.read()
                kind = response.headers.get("Content-Type", "")
            page = body.decode("utf-8", "replace")
            out.append(f"## Request {ngo_id}: {kind}, {len(body)} bytes\n```\n" + "\n".join(masked_outline(page)[:170]) + "\n```")
        except Exception as error:
            out.append(f"## Request {ngo_id}: failed: {error}")

    for bill in BILLS:
        url = f"{BASE}/BillInfo.aspx?s=26RS&b={bill}&sbi=y"
        try:
            page = fetch(url, "louisiana_bills", refresh=True).decode("utf-8", "replace")
        except Exception as error:
            out.append(f"## {bill}: failed: {error}")
            continue
        title = re.search(r"(Act No\.?\s*\d+|Becomes Act[^<]{0,40})", page, re.I)
        links = [(html_lib.unescape(h), re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", "", t))).strip()) for h, t in re.findall(r'<a[^>]+href="([^"]*ViewDocument[^"]*)"[^>]*>(.*?)</a>', page, re.S)]
        summary = re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", " ", page)))
        at = summary.find("Current Status")
        out.append(f"## {bill}: {len(page)} bytes, {title.group(1) if title else 'no act text'}\n{summary[at:at + 320] if at >= 0 else ''}\n```\n" + "\n".join(f"{t[:50]} -> {h}" for h, t in links[:40]) + "\n```")

    fetch("https://legis.la.gov/legis/ViewDocument.aspx?d=1481867", "louisiana_acts", refresh=True)
    out.append("Saved Act 961 to data/raw/louisiana_acts.")
    pathlib.Path(os.environ.get("GITHUB_STEP_SUMMARY", "la_probe.md")).write_text("\n\n".join(out), encoding="utf-8")
    print("\n\n".join(out)[:2000])


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
