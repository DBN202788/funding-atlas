"""Saves Utah's public Requests for Appropriation, which this project's owner can't reach directly.

Usage on GitHub: the "Fetch a state's public source" workflow, with script ut_fetch.py.
The list names projects, legislators and amounts, with no private contact details.
"""
import pathlib
import sys
import time
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from fetch import USER_AGENT  # noqa: E402

OUT = HERE.parent / "data" / "raw" / "utah"
DELAY_SECONDS = 10  # le.utah.gov asks some crawlers for this delay, so use it for all requests.
PAGES = {
    "requests_all.html": "https://le.utah.gov/sessions/requestsAppropSpon.jsp?sess=all&comm=&groupBy=legislator&",
    "abcs_of_rfas.html": "https://budget.utah.gov/the-abcs-of-rfas/",
    "joint_rule_701.html": "https://le.utah.gov/xcode/TitleJR3/Chapter2/JR3-2-S701.html",
    "joint_rule_702.html": "https://le.utah.gov/xcode/TitleJR3/Chapter2/JR3-2-S702.html",
}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("sample_*.html"):
        old.unlink()
    for name, url in PAGES.items():
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=120) as response:
                body = response.read()
            (OUT / name).write_bytes(body)
            print("saved", name, len(body))
        except Exception as error:
            print("could not save", url, error)
        time.sleep(DELAY_SECONDS)


if __name__ == "__main__":
    main()
