"""Checks whether Utah's request list can be reached from GitHub's servers. Prints only what's needed."""
import re
import urllib.error
import urllib.request

USER_AGENT = "LegislativeFundingAtlas/1.0 (public-records research; info@dream-buildersnetwork.com)"
PAGES = ["https://le.utah.gov/robots.txt", "https://le.utah.gov/sessions/requestsAppropSpon.jsp", "https://le.utah.gov/sessions/requestsAppropSpon.jsp?fund=", "https://budget.utah.gov/the-abcs-of-rfas/"]

for url in PAGES:
    try:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read().decode("utf-8", "replace")
        print("OK", response.status, len(body), url)
        if "robots" in url:
            print(body[:500])
        else:
            text = re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>", " ", body, flags=re.S | re.I)
            print(re.sub(r"\s+", " ", text)[:900])
            print("links:", re.findall(r'href="([^"]*(?:request|approp|fund|rfa|year|session)[^"]*)"', body, flags=re.I)[:25])
    except urllib.error.HTTPError as error:
        print("HTTP", error.code, url)
    except Exception as error:
        print("FAILED", url, error)
