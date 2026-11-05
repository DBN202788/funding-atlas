# Legislative Funding Atlas

Helps nonprofits see what was requested from state legislatures, what reached a budget,
and what to prepare before applying. Everything links back to the source document.

## Project layout

| Path | Purpose |
|---|---|
| `pipeline/fetch.py` | Polite, cached downloader. It respects each site's crawl delay. |
| `pipeline/states/` | One adapter per state. Add a state by adding an adapter. |
| `pipeline/states/florida_budget.py` | Links Florida requests to budgets and vetoes. |
| `pipeline/tagging.py` | Keyword tags for program area and recipient type. |
| `pipeline/build.py` | Builds all outputs. |
| `pipeline/verify.py` | Independent checks. Run after every build. |
| `pipeline/ocr_windows.ps1` | OCR for scanned veto messages, using Windows' built-in engine. |
| `data/raw/` | Cached source documents (can be re-downloaded). |
| `data/manual/` | OCR text of the Florida FY2026-27 veto message. |
| `data/out/` | `funding.sqlite` and `funding.csv`. |
| `web/` | The static web app. |

## Run it

The full update job refreshes sources, checks the result and publishes only if the checks pass:

```bash
python -I pipeline/update.py
```

Add `--offline` to use saved copies only. Then build the public website into `dist/`:

```bash
python -I pipeline/site.py
```

Run the tests:

```bash
python -I tests/test_update.py
```

```bash
python -I tests/test_end_to_end.py
```

Download Florida request forms (resumable, about 8 hours per year at the site's crawl delay):

```bash
python -I pipeline/download_forms.py FY2026-27
```

PDF reading needs PyMuPDF (`pip install --user pymupdf`). See [docs/PUBLIC_LAUNCH.md](docs/PUBLIC_LAUNCH.md) to go public.

## How updates work

`.github/workflows/update.yml` runs every Monday once the project is on GitHub:

1. Downloads up to 500 new request forms.
2. Builds a new dataset in `data/staging`, from fresh copies of recent sources.
3. Runs `verify.py` and compares with the published data. New records are listed for the site.
4. Publishes only if every check passes. If a source is down, its last saved copy is used and flagged.
5. Updates `data/out/review.md` and a GitHub issue with anything a person should look at.
6. Rebuilds and deploys the website. A held-back update redeploys the previous data and emails the owner.

The job finds new Florida years, likely new Florida budgets, and new Delaware acts on its own. It can't process veto letters, write new state adapters or change checklists, so those appear on the review list.

## Rules for every adapter

1. A number is published only with a link to its source.
2. If a source has printed totals, the parsed data has to match them, or that year is held back.
3. Prefer exact identifiers over name matching. Anything uncertain gets its own status.
4. An appropriation is not a payment, and vetoes are tracked separately.
5. Don't copy personal contact details from filed forms into published data.

## Status

| State | Data | Remaining work |
|---|---|---|
| Florida | 19,178 Senate requests, FY2017-18 to FY2026-27. 10,262 linked to enrolled budgets by form number. | Veto messages for years before FY2026-27. House requests. Review 14 uncertain vetoes. |
| Delaware | 4,575 appropriations for FY2019, 2021, 2022, 2025, 2026, 2027. | Fix parsing for FY2017, 2018, 2020, 2023, 2024, which don't yet match printed totals. |
| Louisiana | 227 NGO requests from the 2026 session, matched to Acts 3, 4 and 961 by name, with vetoes applied. | Load earlier sessions. Check 8 possible matches by hand. `legis.la.gov` fails DNS lookups on the build machine, so it's read only through GitHub's servers; use the Louisiana setup workflow for manual runs. New sessions need their budget bills added to `SESSION_BILLS`. |
| Utah | None yet. | `le.utah.gov` could not be reached from the build machine. |
| Hawaii | None yet. | The site blocks automated access. Use a manual export or ask for permission. |

## Known limits

- Florida's request list has project titles, not recipient names. Forms add the recipient and its real type,
  but only FY2026-27 is being read so far. Other years still use keyword guesses.
- The update job hasn't run on GitHub yet. Sites may treat a data center differently from this computer, so
  watch the first runs. Florida's site has already returned rate limits and timeouts.
- "No budget citation" means no Senate form number was found. A project could still be funded through a
  House request or under other wording.
- Payment data is not loaded. A next source would be each state's spending or checkbook site.
- The Florida veto message is a third-party hosted copy of the Governor's letter. Replace it with an
  official copy if one can be found.
