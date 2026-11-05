"""Builds the public website into dist/, ready for any static web host.

web/index.html holds only the page content, so this adds the full document around it.
Usage: python -I pipeline/site.py
"""
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
DIST = ROOT / "dist"
DESCRIPTION = "Search what nonprofits and communities asked state legislatures to fund, see what reached a budget, and prepare your own application."


def build():
    if not (WEB / "data" / "records.json").exists():
        raise SystemExit("Run pipeline/build.py or pipeline/update.py first.")
    shutil.rmtree(DIST, ignore_errors=True)
    (DIST / "data").mkdir(parents=True)

    body = (WEB / "index.html").read_text(encoding="utf-8")
    # The content file starts with its own charset and viewport tags, which belong in the head.
    head_tags, rest = [], body
    while rest.startswith("<meta") or rest.startswith("<title") or rest.startswith("<link"):
        line, _, rest = rest.partition("\n")
        head_tags.append(line)
    style_end = rest.index("</style>") + len("</style>")
    style, content = rest[:style_end], rest[style_end:]
    head = "\n".join(head_tags)
    page = f"""<!doctype html>
<html lang="en">
<head>
{head}
<meta name="description" content="{DESCRIPTION}">
<meta property="og:title" content="Legislative Funding Atlas">
<meta property="og:description" content="{DESCRIPTION}">
<meta property="og:type" content="website">
{style}
<style>body {{ margin: 0; }} [hidden] {{ display: none !important; }} img {{ max-width: 100%; }}</style>
</head>
<body>
{content}
</body>
</html>
"""
    (DIST / "index.html").write_text(page, encoding="utf-8")
    for item in (WEB / "data").iterdir():
        shutil.copy2(item, DIST / "data" / item.name)
    shutil.copy2(ROOT / "data" / "out" / "funding.csv", DIST / "data" / "funding.csv")
    (DIST / "robots.txt").write_text("User-agent: *\nAllow: /\n", encoding="utf-8")
    (DIST / ".nojekyll").write_text("", encoding="utf-8")
    size = sum(p.stat().st_size for p in DIST.rglob("*") if p.is_file())
    print(f"Built dist/ ({size / 1e6:.1f} MB)")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    build()
