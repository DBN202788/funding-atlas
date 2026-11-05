"""Runs the real update job against simulated situations, then restores everything.

Scenario 1: new applications appear. They should publish and be reported as new.
Scenario 2: an update would lose too many records. It should be held back and leave the site untouched.

Run: python -I tests/test_end_to_end.py
"""
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
WEB = ROOT / "web" / "data"
OUT = ROOT / "data" / "out"
failures = 0


def check(label, passed, detail=""):
    global failures
    print(("PASS " if passed else "FAIL ") + label + (f"  [{detail}]" if detail else ""))
    failures += 0 if passed else 1


def run_update():
    return subprocess.run([sys.executable, "-I", str(ROOT / "pipeline" / "update.py"), "--offline"], capture_output=True, text=True, encoding="utf-8", errors="replace")


def digest(path):
    return hashlib.sha1(path.read_bytes()).hexdigest()


backup = pathlib.Path(tempfile.mkdtemp())
shutil.copytree(WEB, backup / "web")
shutil.copytree(OUT, backup / "out")
records_file = WEB / "records.json"
try:
    # Scenario 1: pretend the last published data was missing five records.
    payload = json.loads(records_file.read_text(encoding="utf-8"))
    removed_ids = [row[18] for row in payload["rows"][:5]]
    payload["rows"] = payload["rows"][5:]
    records_file.write_text(json.dumps(payload), encoding="utf-8")
    result = run_update()
    meta = json.loads((WEB / "meta.json").read_text(encoding="utf-8"))
    check("new applications publish", result.returncode == 0 and "PUBLISHED: 5 new" in result.stdout, result.stdout.strip().splitlines()[-1])
    check("new applications are listed for the website", sorted(meta["changes"]["new"]) == sorted(removed_ids))
    check("new records are in the published data", len(json.loads(records_file.read_text(encoding="utf-8"))["rows"]) == meta["record_count"])

    # Scenario 2: pretend the last published data had thousands more records.
    payload = json.loads(records_file.read_text(encoding="utf-8"))
    template = payload["rows"][0]
    payload["rows"] += [template[:18] + [f"FAKE-{i}"] + template[19:] for i in range(5000)]
    records_file.write_text(json.dumps(payload), encoding="utf-8")
    before = digest(records_file)
    result = run_update()
    review = (OUT / "review.md").read_text(encoding="utf-8")
    check("a damaging update is held back", result.returncode == 1 and "HELD BACK" in result.stdout, result.stdout.strip().splitlines()[-2][:120])
    check("the published file is untouched when held back", digest(records_file) == before)
    check("the review list explains the hold", "held back" in review and "disappear" in review)
    check("staging is cleaned up", not (ROOT / "data" / "staging").exists())
finally:
    shutil.rmtree(WEB)
    shutil.rmtree(OUT)
    shutil.copytree(backup / "web", WEB)
    shutil.copytree(backup / "out", OUT)
    shutil.rmtree(backup)

print("\nAll scenarios passed" if not failures else f"\n{failures} scenarios failed")
sys.exit(1 if failures else 0)
