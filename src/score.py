import re as _re

_PAY_WORDS = _re.compile(r"\b(?:salary|pay|compensation|base|wage|hourly|annual|range|per (?:hour|year)|/\s?(?:hr|hour|yr|year))\b", _re.I)
_RANGE = _re.compile(r"\$\s*(\d[\d,]*(?:\.\d+)?)\s*(k)?\s*(?:-|–|—|to)\s*\$?\s*(\d[\d,]*(?:\.\d+)?)\s*(k)?", _re.I)
_BIG_UNIT = _re.compile(r"\s*(?:million|billion|mm\b|m\b|bn\b)", _re.I)


def _amount(text, thousands):
    try:
        return float(text.replace(",", "")) * (1000 if thousands else 1)
    except ValueError:
        return None


def _salary_from_text(desc):
    """Pull an annual USD range like '$79,167.00 To $95,000.00' or '$80k - $95k' out of free text. A range followed by
    million or billion is a budget, not pay. When several ranges remain, the one next to a pay word wins."""
    ranges = []
    for m in _RANGE.finditer(desc):
        if _BIG_UNIT.match(desc, m.end()):
            continue
        a, b = _amount(m.group(1), m.group(2)), _amount(m.group(3), m.group(4))
        if a is None or b is None:
            continue
        if a < 1000 and b < 1000:        # hourly
            a, b = a * 2080, b * 2080
        if a < 20000 or b > 1_000_000:   # implausible annual figure; ignore
            continue
        ranges.append((m, a, b))
    for m, a, b in ranges:
        near = desc[max(0, m.start() - 60):m.start()] + " " + desc[m.end():m.end() + 40].split(". ")[0]
        if _PAY_WORDS.search(near):
            return a, b
    return (ranges[0][1], ranges[0][2]) if ranges else (None, None)

"""Hard filters + fit score. evaluate() -> (score 0-100, notes, reject_reason|None).

Design: a job is APPLY-ELIGIBLE only if it has a description (so years-required and
role content were actually checked), passes hard filters, and scores >= MIN_APPLY_SCORE.
"""
import re

MIN_APPLY_SCORE = 50

# Every United States state by abbreviation. The candidate's home state comes from the profile, never from this file.
STATE_NAMES = {
    "AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas", "CA": "california", "CO": "colorado",
    "CT": "connecticut", "DE": "delaware", "DC": "district of columbia", "FL": "florida", "GA": "georgia",
    "HI": "hawaii", "ID": "idaho", "IL": "illinois", "IN": "indiana", "IA": "iowa", "KS": "kansas",
    "KY": "kentucky", "LA": "louisiana", "ME": "maine", "MD": "maryland", "MA": "massachusetts",
    "MI": "michigan", "MN": "minnesota", "MS": "mississippi", "MO": "missouri", "MT": "montana",
    "NE": "nebraska", "NV": "nevada", "NH": "new hampshire", "NJ": "new jersey", "NM": "new mexico",
    "NY": "new york", "NC": "north carolina", "ND": "north dakota", "OH": "ohio", "OK": "oklahoma",
    "OR": "oregon", "PA": "pennsylvania", "RI": "rhode island", "SC": "south carolina", "SD": "south dakota",
    "TN": "tennessee", "TX": "texas", "UT": "utah", "VT": "vermont", "VA": "virginia", "WA": "washington",
    "WV": "west virginia", "WI": "wisconsin", "WY": "wyoming",
}
_NAME_TO_ABBR = {name: abbr.lower() for abbr, name in STATE_NAMES.items()}

# Regions and time zones that contain a state. A posting limited to one of these is not an exclusion for that state's residents.
_EASTERN = {"CT", "DE", "DC", "FL", "GA", "IN", "KY", "ME", "MD", "MA", "MI", "NH", "NJ", "NY", "NC", "OH", "PA", "RI", "SC", "VT", "VA", "WV"}
_NEW_ENGLAND = {"CT", "ME", "MA", "NH", "RI", "VT"}
_REGION_WORDS = {
    s: (("eastern", "east coast", "est", "et") if s in _EASTERN else ()) + (("northeast", "new england") if s in _NEW_ENGLAND else ())
    for s in STATE_NAMES
}


def home_state(profile):
    """The candidate's home state abbreviation from the profile (candidate.state, else the end of candidate.location)."""
    candidate = (profile or {}).get("candidate") or {}
    state = str(candidate.get("state") or "").strip().upper()
    if state not in STATE_NAMES:
        found = re.search(r",\s*([A-Za-z]{2})\s*$", str(candidate.get("location") or ""))
        state = found.group(1).upper() if found else ""
    return state if state in STATE_NAMES else None


def _state_pattern(state):
    """Regex for a state's full name or abbreviation (text is lower case). Never matches when the state is unknown."""
    return rf"(?:{re.escape(STATE_NAMES[state])}|{state.lower()})" if state else r"(?!)"


# Senior analyst IC levels are configurable; management and higher levels never are.
TOO_SENIOR = ["manager", "director", "lead", "managing", "principal", "consultant", "specialist ii"]


def _has(text, term):
    return re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", text) is not None



def _title_excludes(bad, title, padded):
    """Entries with a leading or trailing space (such as 'vp ') are literal fragments. Plain words match whole words only,
    so 'intern' excludes 'Intern' and 'Internship' but not 'Internal Medicine'."""
    if bad != bad.strip():
        return bad in padded
    return re.search(r"(?<![a-z0-9])" + re.escape(bad) + r"(?:s|ship|ships|ing)?(?![a-z0-9])", title) is not None


_YEARS = re.compile(r"(?<![\d.$])(\d{1,2})(?:\s*(?:-|–|to)\s*(\d{1,2}))?\s*\+?\s*(?:years|yrs)\b(?!\s*(?:of age|old))")
_EXPERIENCE_WORDS = re.compile(r"\b(?:experience|experienced|background|expertise|proven|track record|hands[- ]on|professional|requires?|required|minimum|at least|must have)\b")
_OPTIONAL_WORDS = re.compile(r"\b(?:plus|preferred|prefer|nice to have|bonus|desirable|ideal|not required|not necessary|optional)\b")


def _required_years(desc):
    """(years, clause): the largest experience requirement in the description. For a range such as '5-10 years' the lower
    bound is the requirement. Age limits, company history and optional ('a plus') clauses are not requirements."""
    best, where = 0, ""
    for m in _YEARS.finditer(desc):
        start = max(desc.rfind(c, 0, m.start()) for c in ".;\n") + 1
        ends = [i for i in (desc.find(c, m.end()) for c in ".;\n") if i >= 0]
        clause = desc[start:min(ends) if ends else len(desc)]
        if not _EXPERIENCE_WORDS.search(clause) or _OPTIONAL_WORDS.search(clause):
            continue
        years = int(m.group(1))
        if years > best:
            best, where = years, clause
    return best, where

def _is_remote(job):
    loc = (job.get("location") or "").lower()
    if re.search(r"\b(?:hybrid|on[- ]?site)\b", loc):
        return False
    return bool(job.get("remote")) or _has(loc, "remote")


def _remote_eligible(job, home):
    """Is a remote posting open to a candidate living in `home` (a state abbreviation, or None when unknown)?"""
    loc = (job.get("location") or "").lower()
    desc = (job.get("description") or "").lower()
    text = f"{loc}\n{desc}"
    ma = _state_pattern(home)
    # Restrictions must concern work/residency, not unrelated benefit prose.
    exclusions = (
        rf"\b(?:excluding|except|not hiring in|not available (?:in|to)|not eligible (?:in|for))\b[^\n.;]{{0,100}}\b{ma}\b",
        rf"\b(?:cannot|can not|unable to|do not|does not)\s+(?:currently\s+)?(?:hire|employ|consider|accept)[^\n.;]{{0,100}}\b{ma}\b",
        # "<State> not excluded" says the opposite of "<State> excluded": a negated keyword is not a ban.
        rf"\b{ma}\b[^\n.;]{{0,40}}(?<!\bnot )(?<!\bnever )(?<!n't )(?<!\bno longer )\b(?:excluded|ineligible)\b",
        rf"\b{ma}\b[^\n.;]{{0,40}}\bnot eligible\b",
    )
    if any(re.search(p, text) for p in exclusions):
        return False
    us = r"(?:united states(?: of america)?|u\.?s\.?a?\.?)"
    residence_scope = False
    # Explicit lists of eligible states must include the home state.
    for match in re.finditer(
        r"(?:eligible states|(?:only|must)\s+(?:be\s+)?(?:reside|residing|live|located|based)\s+in|"
        r"only\s+(?:candidates|applicants|residents)\s+(?:located in|based in|from|in)|"
        r"(?:remote work|(?:this|the)\s+(?:position|role|job))\s+(?:is\s+)?(?:limited|restricted)\s+to|"
        r"(?:limited|restricted|open|available)\s+(?:only\s+)?to\s+(?:residents|candidates|applicants)\s+(?:of|in)|"
        r"(?:reside|live|located|based)\s+in\s+(?:one of\s+)?(?:the\s+)?following states\s*:?)"
        r"\s*([^\n.;]+)", text
    ):
        region = match.group(1)
        # A time zone or region that includes the home state is not an exclusion. Other zones are, and the worker still checks the employer's rule.
        words = _REGION_WORDS.get(home, ())
        if words and re.search(r"\b(?:" + "|".join(words) + r")\b", region):
            residence_scope = True
            continue
        if not re.search(rf"\b{ma}\b|(?<!\w){us}(?!\w)", region):
            return False
        residence_scope = True
    foreign = r"(?:canada|united kingdom|uk|india|australia|europe|european union|eu|emea|apac)"
    restriction = re.compile(
        rf"\b(?:remote|reside|located|based|work)\s+(?:only\s+)?(?:in|within|from)\s+(?:the\s+)?{foreign}\b|"
        rf"\b{foreign}[- ]only\b")
    for hit in restriction.finditer(text):
        before = text[max(text.rfind(c, 0, hit.start()) for c in ".;\n") + 1:hit.start()]
        # "Our engineering team is based in India" describes the employer, not where the candidate must live.
        if re.search(r"\b(?:team|teams|office|offices|headquarter\w*|hq|company|clients?|customers?|partners?|vendors?|colleagues)\b", before):
            continue
        return False
    # A foreign location is not made US-eligible by a passing US reference in the description.
    clean_loc = re.sub(r"\bremote\b|[()]", "", loc).strip(" ,:-")
    us_location = bool(re.search(rf"(?<!\w){us}(?!\w)", clean_loc))
    ma_location = bool(re.search(rf"\b{ma}\b", clean_loc))
    if _has(clean_loc, "only") and not ma_location:
        national_only = re.sub(rf"(?<!\w){us}(?!\w)|\bonly\b|[(),:-]", "", clean_loc).strip()
        if national_only:
            return False
    national_scope = bool(re.search(
        rf"(?:remote\s+(?:within|across|in|throughout)\s+(?:the\s+)?{us}|"
        rf"(?:anywhere|nationwide)\s+in\s+(?:the\s+)?{us}|"
        rf"(?:{us})[- ]based\s+remote|(?<!\w){us}[- ]only|"
        rf"(?:based|located|reside|residing|live|living)\s+in\s+(?:the\s+)?{us})", desc))
    # A known US remote tag is sufficient for screening unless an explicit restriction above excludes the home state.
    # The worker must still verify the employer's actual residency requirements before Submit.
    if _us_location(job):
        return True
    if clean_loc and not us_location and not ma_location and not (residence_scope or national_scope):
        # A posting tagged to another named place says nothing about the home state until its own text does.
        return False
    return us_location or ma_location or residence_scope or bool(re.search(
        rf"(?:remote\s+(?:within|across|in|throughout)\s+(?:the\s+)?{us}|"
        rf"(?:anywhere|nationwide)\s+in\s+(?:the\s+)?{us}|"
        rf"(?:{us})[- ]based\s+remote|(?<!\w){us}[- ]only|"
        rf"(?:based|located|reside|residing|live|living)\s+in\s+(?:the\s+)?{us})", desc
    ))


def _abbreviate_states(text):
    names = "|".join(re.escape(name) for name in sorted(_NAME_TO_ABBR, key=len, reverse=True))
    return re.sub(rf"\b({names})\b", lambda m: _NAME_TO_ABBR[m.group(1)], text)


def _whitelisted_city(location, locations):
    """Match explicit city/state entries, never a city substring or a distance estimate."""
    loc = re.sub(r"\b(?:hybrid|on[- ]?site)\b|[()]", "", location.lower()).strip(" ,:-")
    city_part, comma, state_part = loc.partition(",")
    loc = city_part.strip() + (", " + _abbreviate_states(state_part.strip()) if comma else "")
    for entry in locations:
        city, _, state = entry.lower().partition(",")
        state = _abbreviate_states(state.strip())
        suffix = rf"(?:,\s*{re.escape(state)})?" if state else ""
        pattern = rf"{re.escape(city.strip())}{suffix}(?:,\s*(?:united states|usa|us))?"
        if re.fullmatch(pattern, loc):
            return True
    return False


# LinkedIn writes many US locations as a metro area with no state code ("Greater Chicago", "San Francisco Bay Area").
# The shape alone is not evidence of a US city ("Greater London Area", "Greater Sydney Area" look identical), so only
# these NAMED US metros count. A company name or any unlisted place in the location field never does. Extend the
# list when a real US metro appears; failing closed is the safe direction.
_US_METROS = (
    "boston", "new york city", "new york", "san francisco bay", "los angeles", "san diego", "chicago", "houston",
    "dallas-fort worth", "dallas", "philadelphia", "phoenix", "washington dc-baltimore", "washington dc",
    "milwaukee", "atlanta", "seattle", "denver", "miami", "minneapolis", "detroit", "pittsburgh", "cleveland",
    "columbus", "cincinnati", "indianapolis", "nashville", "charlotte", "raleigh", "austin", "san antonio",
    "las vegas", "portland", "sacramento", "salt lake city", "kansas city", "st. louis", "tampa", "orlando",
    "baltimore", "hartford", "providence", "albuquerque-santa fe", "columbia, south carolina", "richmond",
    "new haven", "worcester", "buffalo", "rochester", "albany", "louisville", "memphis", "oklahoma city",
)
_METRO_AREA = re.compile(
    r"^(?:greater\s+)?(?P<name>" + "|".join(re.escape(m) for m in _US_METROS) + r")"
    r"(?:\s+(?:metropolitan\s+area|metroplex|area))?\s*(?:\((?:on-?site|hybrid|remote)\))?$")


def _us_location(job):
    """True when the posting is located in the United States (any state), or is US remote."""
    location = job.get("location") or ""
    loc = location.lower()
    if re.search(r"\b(?:canada|united kingdom|uk|india|ireland|germany|france|mexico|philippines|australia|singapore|"
                 r"emea|apac|latam|europe|asia)\b", loc):
        return False
    if re.search(r"\b(?:united states|usa|u\.s\.)\b", loc):
        return True
    states = (r"AL|AK|AZ|AR|CA|CO|CT|DE|DC|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|"
             r"NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY")
    if re.search(rf",\s*(?:{states})\b", location):
        return True
    return _METRO_AREA.fullmatch(loc.strip()) is not None


def location_ok(job, prefs, home=None):
    """`home` is the candidate's home state abbreviation from the profile, or None when the profile gives none."""
    if prefs.get("location_mode") == "united_states":
        if _is_remote(job):
            return bool(prefs.get("accept_remote")) and _remote_eligible(job, home)
        return _us_location(job)  # onsite/hybrid anywhere in the US: the candidate is willing to relocate
    if _is_remote(job):
        return bool(prefs.get("accept_remote")) and _remote_eligible(job, home)
    if not _whitelisted_city(job.get("location") or "", prefs.get("locations", [])):
        return False
    distance, limit = job.get("distance_miles"), prefs.get("max_commute_miles")
    if distance is not None and limit is not None:
        try:
            return 0 <= float(distance) <= float(limit)
        except (TypeError, ValueError):
            return False
    return True


def _employment_rejection(job, prefs):
    if "permanent_full_time" not in prefs.get("employment_types", []):
        return None
    employment = re.sub(r"[_-]", " ", str(job.get("employment_type") or "").lower())
    incompatible = r"\b(?:part\s*time|contract(?:or)?|temporary|temp|intern(?:ship)?|co[\s-]?op|freelance|per diem|seasonal|fixed term)\b"
    if re.search(incompatible, employment):
        return "employment_type"
    title = re.sub(r"[_-]", " ", (job.get("title") or "").lower())
    if re.search(incompatible, title):
        return "employment_type"
    desc = (job.get("description") or "").lower()
    # Require a role-specific assertion: benefit references to part-time employees,
    # contract management, or short-term disability do not describe this job's type.
    role_patterns = (
        r"(?:employment|job|position)\s*type\s*:\s*([^\n.;]+)",
        r"\b(?:this|the)\s+(?:position|role|job)\s+(?:is|will be)\s+(?:a[n]?\s+)?"
        r"((?:part[- ]time|contract(?:or)?|temporary|temp|internship|co[- ]op|freelance|per diem|seasonal|fixed[- ]term)\b)",
        r"\b(?:a[n]?\s+)?((?:part[- ]time|temporary|contract|fixed[- ]term|seasonal|internship|co[- ]op))\s+(?:position|role|job|opportunity)\b",
        r"\b((?:\d+[- ](?:month|week)|\d+\s+(?:months|weeks))\s+contract)\b",
    )
    for pattern in role_patterns:
        for match in re.finditer(pattern, desc):
            assertion = re.sub(r"[_-]", " ", match.group(1))
            if re.search(incompatible, assertion):
                return "employment_description"
    return None


def evaluate(job, profile):
    prefs, s = profile["preferences"], profile["search"]
    title = (job["title"] or "").lower()
    padded = f" {title} "
    desc = (job.get("description") or "").lower()
    blob = f"{title} {desc}"
    # The candidate's field comes from profile.search, not from this file. Absent lists mean no extra signal.
    role_words = [w.lower() for w in s.get("role_words", ["analyst"])]
    core_words = [w.lower() for w in (s.get("core_title_words") or s.get("title_include", []))]
    context_words = [w.lower() for w in s.get("context_words", [])]
    domain_words = [w.lower() for w in s.get("domain_words", [])]

    if job.get("closed"):
        return 0, "", "closed"
    if not location_ok(job, prefs, home_state(profile)):
        return 0, "", "location"
    employment_reject = _employment_rejection(job, prefs)
    if employment_reject:
        return 0, "", employment_reject
    senior_ic = prefs.get("allow_senior_ic", False) and any(_has(title, r) for r in role_words)
    for bad in s["title_exclude"]:
        if senior_ic and bad.strip() in ("senior", "sr", "sr.", "iii"):
            continue
        if _title_excludes(bad, title, padded):
            return 0, "", f"title_excluded:{bad.strip()}"
    for bad in TOO_SENIOR:
        if _has(title, bad):
            return 0, "", f"too_senior:{bad.strip()}"
    if re.search(r"\b(?:senior|sr\.?)(?![a-z0-9])", title) and not senior_ic:
        return 0, "", "too_senior:senior"
    # III is an optional analyst IC level; IV/V remain outside this campaign.
    if re.search(r"(?<![a-z0-9])(?:iv|v)(?![a-z0-9])", title):
        return 0, "", "too_senior:level_iv+"
    if re.search(r"(?<![a-z0-9])[il]{3}(?![a-z0-9])", title) and not senior_ic:
        return 0, "", "too_senior:level_iii"
    for bad in s.get("title_out_of_lane", []):
        if bad.lower() in padded:
            return 0, "", f"out_of_lane:{bad.strip().lower()}"
    if not any(g in title for g in s["title_include"]):
        return 0, "", "title_no_match"

    lo, hi = job.get("salary_min"), job.get("salary_max")
    if not (lo or hi):
        lo, hi = _salary_from_text(desc)
    if lo or hi:
        hi = hi or lo
        lo = lo or hi
        mid = (lo + hi) / 2
        # Reject if the midpoint is below the floor (a range that only touches the floor at its very top is not a fit).
        if mid < prefs["min_salary_usd"]:
            return 0, "", f"salary_below_min:{int(lo)}-{int(hi)}"
    elif prefs.get("unknown_salary") != "allow":
        return 0, "", "salary_unknown"

    # Experience the job requires beyond what the candidate has. Only clauses that describe required experience count.
    required, _clause = _required_years(desc)
    if s["seniority_max_years_required"] < required < 30:
        return 0, "", f"requires_{required}_years"

    score = 0.0
    core = [t for t in core_words if t in title]
    if core:
        score += 40
    elif any(_has(title, r) for r in role_words) and context_words and any(h in blob for h in context_words):
        score += 20  # a generic role title in the candidate's field (profile.search.context_words)
    else:
        score += 5

    skills = [k for k in s.get("skills", []) if _has(blob, k)]
    score += min(40.0, len(skills) * 5)  # 8 distinct skill hits = full 40

    core_domain = sum(1 for k in domain_words if k in blob)
    score += min(15.0, core_domain * 5)

    if "entry level" in blob and not any(_has(title, r) for r in role_words):
        score -= 10
    if job.get("salary_min") or job.get("salary_max"):
        score += 5
    notes = list(skills)
    if not (lo or hi):
        notes.append("salary_unknown:allowed")
    if not _is_remote(job):
        if job.get("distance_miles") is None:
            notes.append("location:verified_city;distance_unverified")
        else:
            notes.append(f"distance_miles:{job['distance_miles']}")
    else:
        notes.append("location:us_remote_eligible")
    return round(min(score, 100.0), 1), ", ".join(notes), None


def explain(job, profile):
    """(reject_reason, plain-English detail) for a job: the text that caused the decision, so a rejection can be reviewed."""
    _, _, reject = evaluate(job, profile)
    if not reject:
        return None, "not rejected"
    desc = (job.get("description") or "").lower()
    title = (job.get("title") or "").lower()
    if reject.startswith("requires_"):
        return reject, "experience clause: " + (_required_years(desc)[1] or "").strip()[:200]
    if reject.startswith("title_excluded:") or reject.startswith("out_of_lane:") or reject.startswith("too_senior:"):
        return reject, f"title '{title}' matched '{reject.split(':', 1)[1]}'"
    if reject == "title_no_match":
        return reject, f"title '{title}' has none of the words in profile.search.title_include"
    if reject.startswith("salary_below_min:"):
        return reject, "salary range used: " + reject.split(":", 1)[1] + " (midpoint below profile.preferences.min_salary_usd)"
    if reject == "location":
        place = job.get("location") or ""
        if _is_remote(job):
            return reject, f"remote posting '{place}' is restricted by residency or region text in the description"
        return reject, f"location '{place}' is not recognised as a US place"
    return reject, reject
