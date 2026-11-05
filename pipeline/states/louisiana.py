"""Louisiana adapter: not parsing yet.

Louisiana is the priority state, but legis.la.gov did not resolve from the build
machine when this was written, so its pages have not been seen directly. Writing
a parser for HTML nobody has inspected would produce numbers that cannot be
trusted. This module only tests access and reports the result, so the state shows
as pending in the coverage table until a real parser is added.

Known source pattern (from search index results):
  Directory: https://www.legis.la.gov/legis/NGO/NgoSearch.aspx?s=26RS
  Request:   https://www.legis.la.gov/legis/NGO/NgoDoc.aspx?NgoId=<id>&search=ALL
  Act 961:   https://legis.la.gov/legis/ViewDocument.aspx?d=1481867
"""
from fetch import fetch

DIRECTORY = "https://www.legis.la.gov/legis/NGO/NgoSearch.aspx?s=26RS"


def collect():
    report = {"state": "LA", "fiscal_year": "2026 Regular Session", "source": DIRECTORY}
    try:
        page = fetch(DIRECTORY, "louisiana", refresh=True)
        report.update(status="reachable_parser_needed", reason=f"Directory returned {len(page)} bytes; the parser still has to be written against it")
    except Exception as error:
        report.update(status="unreachable", reason=str(error))
    return [], [report]
