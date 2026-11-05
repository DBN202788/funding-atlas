"""Tests for the update safety logic and form parsing. Run: python -I tests/test_update.py"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "pipeline"), str(ROOT / "pipeline" / "states")]

import compare  # noqa: E402
import florida_forms as ff  # noqa: E402
import update  # noqa: E402

failures = 0


def check(label, passed):
    global failures
    print(("PASS " if passed else "FAIL ") + label)
    failures += 0 if passed else 1


before = {str(i): {"name": f"r{i}", "requested": 1, "budgeted": 1, "status": "Funded"} for i in range(1000)}

# A new application is detected.
after = dict(before, new1={"name": "new", "requested": 5, "budgeted": None, "status": "Requested"})
diff = update.compare_snapshots(before, after)
check("detects a new application", diff["new"] == ["new1"] and not diff["removed"])
check("a new application doesn't block publishing", not update.safety_problems(before, diff, []))

# A status change, such as a veto, is detected.
after = dict(before)
after["5"] = {"name": "r5", "requested": 1, "budgeted": 0, "status": "Vetoed by Governor"}
check("detects a changed outcome", update.compare_snapshots(before, after)["changed"] == ["5"])

# A broken parser or site that loses data must not publish.
after = {k: v for k, v in before.items() if int(k) >= 500}
diff = update.compare_snapshots(before, after)
check("losing half the records blocks publishing", bool(update.safety_problems(before, diff, [])))
after = {k: v for k, v in before.items() if int(k) >= 5}
diff = update.compare_snapshots(before, after)
check("a few removed records don't block publishing", not update.safety_problems(before, diff, []))

# A failed state blocks publishing.
diff = update.compare_snapshots(before, before)
check("a failed year blocks publishing", bool(update.safety_problems(before, diff, [{"status": "error", "state": "FL", "fiscal_year": "FY2030-31", "reason": "x"}])))

# Contact details are scrubbed from free text.
text = ff.scrub("Call (772) 555-0100 or 772-555-0100 or write jo.doe@example.org about 1,200 youth")
check("scrubs phones and emails", "555" not in text and "example.org" not in text and "1,200 youth" in text)

# Checkbox reading.
options = [("A", 0, 1, "x"), ("B", 0, 2, "y"), ("C", 0, 3, "x"), ("D", 0, 4, "x")]
check("finds the marked checkbox", ff.selected(options) == "B")
check("no mark returns nothing", ff.selected([("A", 0, 1, "x"), ("B", 0, 2, "x")]) is None)

# Comparison.
forms = {str(i): {"match_total": 10 if i < 15 else 0, "total_cost": 100, "org_type": "Nonprofit 501(c)(3)"} for i in range(40)}
records = [{"fiscal_year": "FY", "form_number": str(i), "status": "Funded" if i < 20 else "No budget citation found"} for i in range(40)]
result = compare.compare(records, forms, "FY")
match = next(f for f in result["all"]["features"] if f["key"] == "has_match")
check("comparison counts groups correctly", result["all"]["counts"] == {"funded": 20, "vetoed": 0, "uncited": 20} and match["funded"] == 75 and match["uncited"] == 0)
check("comparison hides percentages for tiny groups", match["vetoed"] is None)

print("\nAll tests passed" if not failures else f"\n{failures} tests failed")
sys.exit(1 if failures else 0)
