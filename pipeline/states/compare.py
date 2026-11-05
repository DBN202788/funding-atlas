"""Compares what funded, vetoed and uncited Florida applications say on their forms.

This describes the applications. It can't show that a feature caused funding:
larger or better-connected groups may simply have more of these features, and
the records don't explain decisions. The published page says so beside the numbers.
"""
FEATURES = [
    ("has_match", "Lists matching funds", lambda f: (f.get("match_total") or 0) > 0),
    ("substantial_match", "Match covers at least half of the project", lambda f: (f.get("total_cost") or 0) > 0 and (f.get("match_total") or 0) / f["total_cost"] >= 0.5),
    ("prior_funding", "Project had state funding before", lambda f: f.get("prior_state_funding") == "Yes"),
    ("future_funding", "Says it will ask again in future years", lambda f: f.get("future_funding") == "Yes"),
    ("agency_contacted", "Contacted the state agency", lambda f: f.get("agency_contacted") == "Yes"),
    ("has_lobbyist", "Lists a lobbyist", lambda f: bool(f.get("has_lobbyist"))),
    ("states_number_served", "States how many people it will serve", lambda f: bool(f.get("states_number_served"))),
    ("numeric_outcome_target", "Sets a percentage outcome target", lambda f: bool(f.get("numeric_outcome_target"))),
    ("names_measurement_method", "Names how outcomes will be measured", lambda f: bool(f.get("names_measurement_method"))),
    ("capital", "Asks for construction or capital money", lambda f: (f.get("capital") or 0) > 0),
]
NONPROFIT = {"Nonprofit 501(c)(3)", "Nonprofit 501(c)(4)"}
GROUPS = {"Funded": "funded", "Vetoed by Governor": "vetoed", "No budget citation found": "uncited"}
MIN_GROUP = 10  # Below this a percentage would mislead, so it's left out.


def _scope(rows):
    counts = {g: sum(1 for _, group in rows if group == g) for g in GROUPS.values()}
    features = []
    for key, label, test in FEATURES:
        entry = {"key": key, "label": label}
        for group in GROUPS.values():
            members = [form for form, g in rows if g == group]
            entry[group] = round(100 * sum(1 for f in members if test(f)) / len(members)) if len(members) >= MIN_GROUP else None
        with_feature = [g for f, g in rows if test(f)]
        without = [g for f, g in rows if not test(f)]
        entry["with_n"], entry["without_n"] = len(with_feature), len(without)
        entry["funded_rate_with"] = round(100 * with_feature.count("funded") / len(with_feature)) if len(with_feature) >= MIN_GROUP else None
        entry["funded_rate_without"] = round(100 * without.count("funded") / len(without)) if len(without) >= MIN_GROUP else None
        features.append(entry)
    return {"counts": counts, "total": len(rows), "features": features}


def compare(records, forms, fiscal_year):
    """Return summary tables for records that have a parsed form, skipping uncertain outcomes."""
    rows = []
    for record in records:
        form = forms.get(record.get("form_number")) if record["fiscal_year"] == fiscal_year else None
        group = GROUPS.get(record["status"])
        if form and group:
            rows.append((form, group))
    by_type = {}
    for form, group in rows:
        entry = by_type.setdefault(form.get("org_type") or "Not marked", {"total": 0, "funded": 0, "vetoed": 0, "uncited": 0})
        entry["total"] += 1
        entry[group] += 1
    return {
        "fiscal_year": fiscal_year,
        "forms_read": len(forms),
        "forms_compared": len(rows),
        "all": _scope(rows),
        "nonprofit": _scope([(f, g) for f, g in rows if f.get("org_type") in NONPROFIT]),
        "by_org_type": [dict(name=k, **v) for k, v in sorted(by_type.items(), key=lambda kv: -kv[1]["total"])],
    }
