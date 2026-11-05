"""Checks that Louisiana parsing keeps program details and drops personal ones."""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "pipeline"), str(ROOT / "pipeline" / "states")]

import louisiana  # noqa: E402

PAGE = """
<span id="ctl00_PageBody_LabelVEntityName">Example Works</span>
<span id="ctl00_PageBody_LabelVEntityPhysicalAddr">123 Private Lane</span>
<span id="ctl00_PageBody_LabelVIncorporators">Jane Doe</span>
<span id="ctl00_PageBody_LabelVTaxpayerIdLast4">1234</span>
<span id="ctl00_PageBody_LabelVOfficers">Jane Doe, John Roe</span>
<span id="ctl00_PageBody_LabelVRequestAmount">$150,000</span>
<span id="ctl00_PageBody_LabelVRequestType">Capital Outlay Appropriation</span>
<span id="ctl00_PageBody_LabelVProjectSummary">Youth program.<br/>Call (504) 555-0100 or a@b.org</span>
<span id="ctl00_PageBody_LabelVBudgetRepairs">$110,000</span>
<span id="ctl00_PageBody_LabelVContactName">Jane Doe</span>
<span id="ctl00_PageBody_LabelVContactEmail">jane@example.org</span>
"""
DIRECTORY = '<td><a href="NgoDoc.aspx?NgoId=3407&amp;search=ALL" target="_blank">18th &amp; Ward</a></td>'
failures = 0


def check(label, passed):
    global failures
    print(("PASS " if passed else "FAIL ") + label)
    failures += 0 if passed else 1


record = louisiana.parse_request(PAGE)
flat = str(record)
check("keeps the name and amount", record["name"] == "Example Works" and record["amount"] == 150000)
check("keeps budget lines", record["budget_repairs"] == 110000)
for private in ("Jane", "John", "Private Lane", "1234", "jane@example.org"):
    check(f"drops {private}", private not in flat)
check("scrubs contact details typed into a summary", "555-0100" not in flat and "a@b.org" not in flat and "Youth program." in flat)
check("reads the directory", louisiana.parse_directory(DIRECTORY) == [("3407", "18th & Ward")])
docs = louisiana.bill_documents('<a href="ViewDocument.aspx?d=1">HB312 Act 961</a><a href="ViewDocument.aspx?d=2">HB312 Veto Message</a><a href="ViewDocument.aspx?d=3">HB312 Enrolled</a>')
check("finds the act and veto message only", set(docs) == {"act", "veto"} and docs["act"][1] == "961")

print("\nAll Louisiana tests passed" if not failures else f"\n{failures} failed")
sys.exit(1 if failures else 0)
