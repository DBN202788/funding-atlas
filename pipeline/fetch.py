"""Polite, cached downloader for public legislative pages.

Every response is saved under data/raw so parsers can be re-run offline and so
each record can be traced back to the exact page it came from.

During a refresh, a site that fails falls back to the saved copy and is recorded
in STALE, so one state's outage can never shrink the published data.
"""
import hashlib
import json
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
USER_AGENT = "FundingAtlasResearch/0.1 (public legislative funding research)"

# Delay per host in seconds. flsenate.gov asks for Crawl-delay: 10 in robots.txt.
CRAWL_DELAY = {"www.flsenate.gov": 10, "flsenate.gov": 10}
DEFAULT_DELAY = 3
_last_hit = {}
STALE = []  # (url, reason) for pages served from the saved copy after a failed refresh.


def cache_path(url, state):
    parsed = urllib.parse.urlparse(url)
    tail = pathlib.PurePosixPath(parsed.path).name or "index"
    digest = hashlib.sha1(url.encode()).hexdigest()[:10]
    return RAW / state / f"{tail[:60]}.{digest}"


def fetch(url, state, refresh=False):
    """Return the bytes for url, using the cache unless refresh is set."""
    path = cache_path(url, state)
    if path.exists() and not refresh:
        return path.read_bytes()

    host = urllib.parse.urlparse(url).netloc
    wait = CRAWL_DELAY.get(host, DEFAULT_DELAY) - (time.time() - _last_hit.get(host, 0))
    if wait > 0:
        time.sleep(wait)

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read()
            content_type = response.headers.get("Content-Type", "")
    except Exception as error:
        if path.exists():
            STALE.append((url, str(error)))
            return path.read_bytes()
        raise
    finally:
        _last_hit[host] = time.time()

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    meta = {"url": url, "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "content_type": content_type}
    path.with_name(path.name + ".meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return body
