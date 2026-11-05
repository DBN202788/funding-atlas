"""Keyword tagging for program category and recipient type.

These tags are automatic best guesses for browsing. They are never presented as
facts from the source documents, and the web app labels them as auto-tagged.
"""
import re

CATEGORIES = [
    ("Youth & Education", r"\b(youth|school|student|education|tutor|mentor|literacy|scholar|college|university|stem|boys (&|and) girls|after.?school|early learning|head start|teacher|academy|children's|kids)\b"),
    ("Health & Disability", r"\b(health|hospital|clinic|medical|mental|behavioral|substance|opioid|addiction|recovery|autism|disabilit|cancer|dental|nursing|hospice|paramedic|ambulance|alzheimer|diabetes|maternal)\b"),
    ("Seniors & Veterans", r"\b(senior|aging|elder|older adult|veteran|american legion|vfw|meals on wheels|military)\b"),
    ("Housing & Human Services", r"\b(housing|homeless|shelter|food|hunger|family|families|domestic violence|foster|human services|community action|social services|pantry|poverty|reentry)\b"),
    ("Arts, Culture & History", r"\b(arts?|museum|theat(er|re)|historic|historical|history|cultural|culture|festival|symphony|library|heritage|orchestra|music)\b"),
    ("Workforce & Economic Development", r"\b(workforce|job|economic|business|entrepreneur|training|apprentice|small business|career|chamber|innovation|tourism)\b"),
    ("Public Safety & Justice", r"\b(police|sheriff|fire|rescue|ems|emergency|justice|crime|court|legal aid|public safety|911|jail|law enforcement)\b"),
    ("Water & Environment", r"\b(water|wastewater|sewer|stormwater|septic|drainage|flood|environment|conservation|restoration|lake|river|beach|resilien|dredg|estuary|coastal|hurricane)\b"),
    ("Infrastructure & Transportation", r"\b(road|street|sidewalk|bridge|transportation|traffic|airport|port|infrastructure|utility|broadband|trail|multi.?use path|corridor|pedestrian)\b"),
    ("Parks & Community Facilities", r"\b(park|recreation|community center|civic center|playground|sports|gym|pool|clubhouse|neighborhood|community)\b"),
]
_COMPILED = [(name, re.compile(pattern, re.I)) for name, pattern in CATEGORIES]


def categorize(*texts):
    """Return the first category whose keywords match, or 'Other'."""
    combined = " ".join(t for t in texts if t)
    for name, pattern in _COMPILED:
        if pattern.search(combined):
            return name
    return "Other"


_GOVERNMENT = re.compile(
    r"\b(city of|town of|village of|county|parish|school district|school board|board of|department of|"
    r"sheriff|police department|fire department|fire district|water district|authority|commission|"
    r"public schools|state college|state university|university of|municipal)\b",
    re.I,
)
_NONPROFIT = re.compile(
    r"\b(inc\.?|incorporated|foundation|association|society|boys (&|and) girls|ymca|ywca|habitat for humanity|"
    r"united way|goodwill|salvation army|catholic charities|ministries|ministry|church|nonprofit|non-profit|"
    r"coalition|alliance|community action|league|friends of|american legion|vfw|senior center|museum|"
    r"food bank|council on aging)\b",
    re.I,
)


def recipient_type(name):
    """Guess the recipient type from a name or project title.

    Nonprofit wording is checked first so that a name such as
    "Boys & Girls Clubs of St. Lucie County" is not read as a county government.
    """
    if _NONPROFIT.search(name or ""):
        return "Likely nonprofit (auto-tagged)"
    if _GOVERNMENT.search(name or ""):
        return "Government (auto-tagged)"
    return "Not determined"
