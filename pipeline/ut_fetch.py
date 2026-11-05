"""Saves Utah's public list of Requests for Appropriation, which this project's owner can't reach directly.

Usage on GitHub: the "Fetch a state's public source" workflow, with script ut_fetch.py.
The list names projects, legislators and amounts, with no private contact details.
"""
import pathlib
import re
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from fetch import USER_AGENT  # noqa: E402

import urllib.request  # noqa: E402

BASE = "https://le.utah.gov"
OUT = HERE.parent / "data" / "raw" / "utah"
DELAY_SECONDS = 10  # le.utah.gov asks some crawlers for this delay, so use it for all requests.


def get(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=90) as response:
        return response.read()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    page = get(f"{BASE}/sessions/requestsAppropSpon.jsp")
    (OUT / "requests_by_sponsor.html").write_bytes(page)
    text = page.decode("utf-8", "replace")
    # Save a sample of linked detail pages, so the parser can be written from the real format.
    links = sorted(set(re.findall(r'href="([^"]*(?:rfa|approp|request|interim)[^"#]*)"', text, flags=re.I)))
    print(len(page), "bytes;", len(links), "related links")
    for link in links[:40]:
        print(link)
    for number, link in enumerate(links[:5]):
        url = link if link.startswith("http") else BASE + ("" if link.startswith("/") else "/sessions/") + link
        time.sleep(DELAY_SECONDS)
        try:
            (OUT / f"sample_{number}.html").write_bytes(get(url))
        except Exception as error:
            print("could not save", url, error)


if __name__ == "__main__":
    main()
