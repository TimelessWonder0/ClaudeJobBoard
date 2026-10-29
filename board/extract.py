"""Pull hard requirements out of job-description text.

Every fact carries the sentence it came from (`evidence`) so the board can show why a role was
hidden or flagged. Nothing here decides to hide a job; filters.py does that from these facts.

Facts extracted:
  years_required / years_preferred   minimum years of experience (required vs preferred text)
  degree_required / degree_preferred bachelors | masters | mba | phd
  certs                              CFA / Series N / SIE / CPA / ...: required | obtainable | preferred | mentioned
  work_mode                          onsite | hybrid | remote | unknown
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

# --------------------------------------------------------------------------------------------
# Result types
# --------------------------------------------------------------------------------------------

CONF_RANK = {"low": 1, "medium": 2, "high": 3}


@dataclass
class YearsFact:
    min_years: float
    evidence: str
    confidence: str = "medium"          # low | medium | high
    routes: list[dict] = field(default_factory=list)   # [{"degree": "bachelors", "years": 3}, ...]
    years_bachelors: float | None = None  # years on the Bachelor's route when routes exist
    skill_specific: list[dict] = field(default_factory=list)  # tool-only years, not counted
    other_bars: list[float] = field(default_factory=list)  # every required figure found


@dataclass
class DegreeFact:
    level: str                          # bachelors | masters | mba | phd
    evidence: str
    confidence: str = "medium"


@dataclass
class CertFact:
    cert: str
    status: str                         # required | obtainable | preferred | mentioned
    evidence: str


@dataclass
class ModeFact:
    mode: str                           # onsite | hybrid | remote | unknown
    evidence: str = ""


@dataclass
class Extraction:
    years_required: YearsFact | None = None
    years_preferred: YearsFact | None = None
    years_skill_specific: list[dict] = field(default_factory=list)
    degree_required: DegreeFact | None = None
    degree_preferred: DegreeFact | None = None
    certs: list[CertFact] = field(default_factory=list)
    work_mode: ModeFact = field(default_factory=lambda: ModeFact("unknown"))
    no_experience_needed: str = ""      # evidence when the JD says entry level / no experience

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------------------------
# Text -> units (section-tagged sentences)
# --------------------------------------------------------------------------------------------

_BULLET = re.compile(r"^[\s•·▪●‣⁃*\-–—]+")
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9•(\"'])")
_INLINE_BULLET = re.compile(r"\s+[•·▪●]\s+")

_PREF_HEAD = re.compile(
    r"\b(preferred|nice[- ]to[- ]have|bonus|desired|beneficial|good to have|additional qualifications|"
    r"extra credit|a plus|plus(es)?|ideal(ly)?)\b", re.I)
_REQ_EXPLICIT_HEAD = re.compile(
    r"\b(basic|minimum|required|must[- ]have|mandatory|core)\b.*\b(qualifications?|requirements?|skills?|experience)\b|"
    r"^(requirements?|qualifications?)\s*(\(required\))?$|\bminimum qualifications?\b|\bbasic qualifications?\b", re.I)
_REQ_GENERIC_HEAD = re.compile(
    r"\b(requirements?|qualifications?|what you('|’)?ll need|what you need|what you bring|"
    r"what we('|’)?re looking for|about you|who you are|you have|you are|your background|"
    r"skills (and|&) experience|experience|the ideal candidate|your profile|candidate profile)\b", re.I)

# Mid-text headings get their own line so section tracking works on flattened JDs.
_HEAD_SPLIT = re.compile(
    r"(?<!\n)\s*\b((?:Basic|Minimum|Required|Preferred|Desired|Additional) Qualifications|"
    r"Qualifications|Requirements|Responsibilities|Nice to Have|Preferred Skills|"
    r"What You(?:'|’)ll Need|What You Bring|About You)\s*[:–—-]\s*(?=\S)")
_HEAD_SPLIT_COLON = re.compile(
    r"(?<!\n)\s+((?:Basic|Minimum|Required|Preferred|Desired|Additional) Qualifications|"
    r"Qualifications|Requirements|Nice to Have):\s*")


@dataclass
class Unit:
    section: str       # required | preferred | neutral
    section_conf: str  # high | medium | low
    text: str


def _classify_heading(h: str) -> tuple[str, str] | None:
    h = h.strip()
    if _PREF_HEAD.search(h):
        return ("preferred", "high")
    if _REQ_EXPLICIT_HEAD.search(h):
        return ("required", "high")
    if _REQ_GENERIC_HEAD.search(h):
        return ("required", "medium")
    return None


_HEAD_EXACT = re.compile(
    r"^(?:(?:basic|minimum|required|preferred|desired|additional|key|core|beneficial)\s+)?"
    r"(?:qualifications?(?:\s*(?:and|&)\s*(?:skills?|experience|requirements?))?|"
    r"requirements?(?:\s*(?:and|&)\s*(?:skills?|experience|qualifications?))?|"
    r"skills?(?:\s*(?:and|&)\s*(?:qualifications?|experience|requirements?))?|"
    r"experience(?:\s*(?:and|&)\s*(?:skills?|qualifications?))?|responsibilities|nice[- ]to[- ]haves?|"
    r"what you(?:'|’)?ll (?:need|do|bring)|what you (?:need|bring|have)|what we(?:'|’)?re looking for|"
    r"about you|who you are|you have|you are|bonus(?: points)?|the ideal candidate)\s*(?:\(.*\))?$", re.I)


def _looks_like_heading(line: str) -> bool:
    s = line.strip().strip("#*_ ")
    if not s or len(s) > 90:
        return False
    if s.endswith(":"):
        return True
    if len(s.split()) <= 7 and not s.endswith((".", ",", ";")):
        return bool(_HEAD_EXACT.match(s)) or (s.isupper() and len(s) > 3)
    return False


def _join_wrapped(lines: list[str]) -> list[str]:
    """Hard-wrapped postings (Google, Workday) split one bullet over several lines; rejoin them.

    A line that starts lowercase and follows a line with no closing punctuation continues it.
    """
    out: list[str] = []
    for raw in lines:
        line = raw.strip()
        if (out and line and out[-1] and line[0].islower()
                and not out[-1].endswith((".", "!", "?", ":", ";"))):
            out[-1] += " " + line
        else:
            out.append(line)
    return out


def to_units(text: str) -> list[Unit]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _HEAD_SPLIT.sub(lambda m: f"\n{m.group(1)}:\n", text)
    text = _HEAD_SPLIT_COLON.sub(lambda m: f"\n{m.group(1)}:\n", text)
    units: list[Unit] = []
    section, conf = "neutral", "low"
    for raw in _join_wrapped(text.split("\n")):
        line = raw.strip()
        if not line:
            continue
        plain = line.strip("#*_ ").rstrip(":").strip()
        stripped_bullet = _BULLET.sub("", line)
        is_bullet = stripped_bullet != line
        if not is_bullet and _looks_like_heading(line):
            cls = _classify_heading(plain)
            if cls:
                section, conf = cls
            else:
                section, conf = "neutral", "low"
            continue
        # "Qualifications: 3+ years ..." (heading with text after the colon)
        m = re.match(r"^\W*([A-Z][A-Za-z '&/’-]{3,50}):\s+(\S.*)$", line)
        if m and not is_bullet:
            cls = _classify_heading(m.group(1))
            if cls:
                section, conf = cls
                line = m.group(2)
                stripped_bullet = line
        body = stripped_bullet.strip("*_ ")
        for chunk in _INLINE_BULLET.split(body):
            for sent in _SENT_SPLIT.split(chunk):
                sent = sent.strip()
                if len(sent) >= 3:
                    units.append(Unit(section, conf, sent))
    return units


# --------------------------------------------------------------------------------------------
# Years
# --------------------------------------------------------------------------------------------

_WORD_NUM = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
             "eight": 8, "nine": 9, "ten": 10, "twelve": 12, "fifteen": 15}
_NUM = r"(?:\d{1,2}(?:\.\d)?|zero|one|two|three|four|five|six|seven|eight|nine|ten|twelve|fifteen)"
_PAREN = r"(?:\s*\(\s*\d{1,2}\s*\))?"
_YEARS_WORD = r"(?:years?|yrs?\.?)"

_RANGE = re.compile(
    rf"(?P<lo>{_NUM}){_PAREN}\s*\+?\s*(?:-|–|—|to)\s*(?P<hi>{_NUM}){_PAREN}\s*\+?\s*{_YEARS_WORD}", re.I)
_SINGLE = re.compile(
    rf"(?P<n>{_NUM}){_PAREN}\s*(?P<plus>\+|or more|plus|and up|and above)?\s*{_YEARS_WORD}(?![a-z])", re.I)

_EXP_WORDS = re.compile(
    r"\b(experience|experienced|background|track record|worked|working|exposure|expertise|"
    r"tenure|in (?:a|an|the)\b|as an?\b|within)\b", re.I)
_YEARS_NOISE_BEFORE = re.compile(
    r"(within|over|in|during|for|across|spanning|covering) the (past|last|next|previous|coming) $|"
    r"(past|last|next|previous) $|(every|each|per) $", re.I)
_YEARS_NOISE_AFTER = re.compile(r"^\s*(old|of age|in business|ago|from now|of history|of data)\b", re.I)

_PREF_INLINE = re.compile(
    r"\b(preferred|a plus|is a plus|are a plus|nice to have|bonus|desired|ideal(?:ly)?|"
    r"an advantage|strongly preferred|highly desirable|desirable)\b", re.I)
_REQ_INLINE = re.compile(r"\b(required|requires?|must|minimum|at least|basic|mandatory|needs?)\b", re.I)
_NO_EXP_STRONG = re.compile(
    r"\b(no (?:prior |previous |relevant )?(?:work )?experience (?:is )?(?:required|necessary|needed))\b", re.I)
_NO_EXP_WEAK = re.compile(
    r"\b(entry[- ]level|new grad(?:uate)?|recent graduates?|early[- ]career)\b", re.I)

_TOOLS = re.compile(
    r"\b(sql|python|tableau|power ?bi|excel|vba|alteryx|hyperion|essbase|anaplan|adaptive|netsuite|sap|"
    r"oracle|looker|snowflake|bloomberg|factset|capital ?iq|macros|vlookup|r\b|java|power ?query|powerpoint)\b", re.I)
_DOMAIN = re.compile(
    r"(financ|fp&a|account|pricing|budget|forecast|analy(?:st|sis|tics)|banking|investment|research|corporate|"
    r"business|audit|model(?:l)?ing|valuation|cost|revenue|operations|planning|strategy|consult)", re.I)

# Degree route forms: "Bachelor's + 3 or Master's + 1", "Bachelor's degree and 3 years ... or Master's and 1 year"
_DEG = (r"(?P<deg>bachelor(?:'|’)?s?(?: degree)?|master(?:'|’)?s?(?: degree)?|mba|ph\.?d\.?|doctorate|"
        r"advanced (?:or graduate )?degree|graduate degree|post-?graduate degree|"
        r"b\.?s\.?c?\.?|b\.?a\.?|m\.?s\.?c?\.?)(?![a-z])(?!\s+(?:excel|office|word|access|project|powerpoint|outlook|dynamics))")
_ROUTE_A = re.compile(
    rf"\b{_DEG}(?:\s+degree)?[^.;\n]{{0,30}}?(?:\+|plus|and|with|combined with)\s*(?:a minimum of |at least )?"
    rf"(?P<n>{_NUM}){_PAREN}\s*\+?\s*{_YEARS_WORD}", re.I)
_ROUTE_B = re.compile(
    rf"(?P<n>{_NUM}){_PAREN}\s*\+?\s*{_YEARS_WORD}[^.;\n]{{0,60}}?\b(?:with|and|plus|\+)\s+(?:an?\s+|relevant\s+|related\s+)*{_DEG}\b", re.I)
_ROUTE_C = re.compile(
    rf"\b{_DEG}\s*\+\s*(?P<n>{_NUM})\b", re.I)


def _to_num(tok: str) -> float:
    tok = tok.lower()
    return float(_WORD_NUM[tok]) if tok in _WORD_NUM else float(tok)


def _deg_level(tok: str) -> str:
    t = tok.lower().replace(".", "").replace("’", "'")
    if t.startswith("bachelor") or t in ("bs", "ba", "bsc"):
        return "bachelors"
    if t == "mba":
        return "mba"
    if t.startswith(("master", "advanced", "graduate", "postgraduate", "post-graduate")) or t in ("ms", "msc"):
        return "masters"
    if t in ("phd", "doctorate"):
        return "phd"
    return "bachelors"


def _evidence(sent: str) -> str:
    s = re.sub(r"\s+", " ", sent).strip()
    return s if len(s) <= 320 else s[:317] + "..."


@dataclass
class _YearsHit:
    lo: float
    sent: str
    start: int
    end: int
    pref: bool
    conf: str
    skill_specific: bool


def _years_hits(unit: Unit) -> list[_YearsHit]:
    s = unit.text
    hits: list[_YearsHit] = []
    used: list[tuple[int, int]] = []

    def overlaps(a: int, b: int) -> bool:
        return any(not (b <= x or a >= y) for x, y in used)

    cands: list[tuple[int, int, float]] = []
    for m in _RANGE.finditer(s):
        cands.append((m.start(), m.end(), _to_num(m.group("lo"))))
        used.append((m.start(), m.end()))
    for m in _SINGLE.finditer(s):
        if overlaps(m.start(), m.end()):
            continue
        cands.append((m.start(), m.end(), _to_num(m.group("n"))))
        used.append((m.start(), m.end()))
    cands.sort()

    has_exp_word = bool(_EXP_WORDS.search(s))
    for i, (a, b, lo) in enumerate(cands):
        before, after = s[max(0, a - 40):a], s[b:b + 80]
        if _YEARS_NOISE_BEFORE.search(before) or _YEARS_NOISE_AFTER.search(after):
            continue
        # Not about experience: needs an experience word nearby, or a qualification section.
        follows = re.match(r"^\s*(?:of|in|as|with|working|within)\b", after, re.I)
        if not (has_exp_word or (unit.section != "neutral" and follows)):
            continue
        next_start = cands[i + 1][0] if i + 1 < len(cands) else len(s)
        tail = s[b:min(next_start, b + 90)]
        head = s[max(0, a - 45):a]
        pref = bool(_PREF_INLINE.search(tail) or re.search(r"(preferred|ideally|desired|bonus)\W{0,3}$", head, re.I))
        if unit.section == "preferred" and not _REQ_INLINE.search(head + tail):
            pref = True
        req_marker = bool(_REQ_INLINE.search(head + tail))
        if unit.section == "required":
            conf = unit.section_conf
            if req_marker:
                conf = "high"
        else:
            conf = "medium" if req_marker else "low"
        # tool-only years ("2+ years of SQL") say nothing about overall experience
        what = re.match(r"^\s*(?:of\s+)?(?:hands[- ]on\s+|professional\s+|relevant\s+|related\s+|work\s+)*"
                        r"(?:experience\s+)?(?:(?:with|in|using|of|on)\s+)?([^,.;:]{0,60})", tail, re.I)
        skill_specific = False
        if what:
            w = what.group(1)
            tool, domain = _TOOLS.search(w), _DOMAIN.search(w)
            # "SQL and Python for data analysis" is tool-specific; "financial analysis using Excel" is not
            if tool and (not domain or tool.start() < domain.start()):
                skill_specific = True
        hits.append(_YearsHit(lo, s, a, b, pref, conf, skill_specific))
    return hits


def _routes(sent: str) -> list[dict]:
    routes: dict[tuple[str, float], None] = {}
    for rx in (_ROUTE_A, _ROUTE_B, _ROUTE_C):
        for m in rx.finditer(sent):
            deg = _deg_level(m.group("deg"))
            n = _to_num(m.group("n"))
            routes[(deg, n)] = None
    return [{"degree": d, "years": y} for d, y in routes]


def extract_years(units: list[Unit]) -> tuple[YearsFact | None, YearsFact | None, list[dict], str]:
    req: list[tuple[float, str, str]] = []          # (years, evidence, conf)
    pref: list[tuple[float, str]] = []
    skills: list[dict] = []
    routes_found: list[dict] = []
    route_sentence = ""
    no_exp = ""
    no_exp_weak = ""

    for u in units:
        if re.search(r"\b\d+\+?\s*years", u.text, re.I):
            pass
        elif not no_exp and _NO_EXP_STRONG.search(u.text):
            no_exp = _evidence(u.text)
        elif not no_exp_weak and _NO_EXP_WEAK.search(u.text):
            no_exp_weak = _evidence(u.text)
        rts = _routes(u.text)
        if rts:
            # "7 years of experience ..., or 5 years with an advanced degree": the unqualified figure is the
            # base route (no higher degree), the qualified one is the degree route.
            qualified = {r["years"] for r in rts}
            for h in _years_hits(u):
                if h.lo not in qualified and not h.pref and not h.skill_specific:
                    rts.append({"degree": "bachelors", "years": h.lo})
        distinct_levels = {r["degree"] for r in rts}
        if len(rts) >= 2 and len(distinct_levels) >= 2 and u.section != "preferred":
            routes_found = rts
            route_sentence = u.text
            continue            # route sentences are resolved below, not as plain years
        for h in _years_hits(u):
            if h.skill_specific:
                skills.append({"years": h.lo, "evidence": _evidence(h.sent)})
            elif h.pref:
                pref.append((h.lo, _evidence(h.sent)))
            else:
                req.append((h.lo, _evidence(h.sent), h.conf))

    years_req: YearsFact | None = None
    if req:
        # Several required bars: take the lowest (an "or" is far more common than an "and"), with the
        # confidence of that bar. The other bars stay visible in `other_bars`.
        years, ev, conf = min(req, key=lambda x: (x[0], -CONF_RANK[x[2]]))
        years_req = YearsFact(years, ev, conf)
        years_req.other_bars = sorted({y for y, _, _ in req})
    if routes_found:
        low = min(r["years"] for r in routes_found)
        bach = [r["years"] for r in routes_found if r["degree"] == "bachelors"]
        route_fact = YearsFact(low, _evidence(route_sentence), "high", routes_found, min(bach) if bach else low)
        if years_req is None or low < years_req.min_years:
            route_fact.other_bars = years_req.other_bars if years_req else []
            years_req = route_fact
        else:
            years_req.routes = routes_found
            years_req.years_bachelors = route_fact.years_bachelors
    years_pref = None
    if pref:
        y, ev = min(pref, key=lambda x: x[0])
        years_pref = YearsFact(y, ev, "medium")
    if years_req is not None:
        years_req.skill_specific = skills
    return years_req, years_pref, skills, no_exp or no_exp_weak


# --------------------------------------------------------------------------------------------
# Degree
# --------------------------------------------------------------------------------------------

_LVL = re.compile(
    r"(?P<bach>bachelor(?:'|’)?s?|undergraduate degree|four[- ]year (?:college )?degree|college degree|"
    r"\bB\.?S\.?c?(?=\s+(?:in|degree)|[/,)])|\bB\.?A\.?(?=\s+(?:in|degree)|[/,)]))|"
    r"(?P<mast>master(?:'|’)?s?|\bMBA\b|graduate degree|advanced degree|post[- ]?graduate|"
    r"\bM\.?S\.?c?\.?(?=\s+(?:in|degree)))|"
    r"(?P<phd>\bph\.?d\.?\b|doctorate|doctoral)", re.I)
_EQUIV = re.compile(r"\b(or equivalent (?:work |practical |professional |relevant |related )*experience|"
                    r"equivalent (?:work |practical |professional |relevant )*experience|equivalent combination|"
                    r"or related experience|or comparable experience|in lieu of)\b", re.I)
_EDU_NOISE = re.compile(r"\b(tuition|reimburse|scholarship|alumni|university recruiting|campus|our (?:team|leaders?)|"
                        r"employees? (?:with|who))\b", re.I)


def _levels_in(sent: str) -> list[tuple[str, int, int]]:
    out = []
    for m in _LVL.finditer(sent):
        if m.group("bach"):
            lvl = "bachelors"
        elif m.group("mast"):
            lvl = "mba" if m.group("mast").lower() == "mba" else "masters"
        else:
            lvl = "phd"
        # "MS Excel", "master data", "master the" etc: Master as a verb/noun, not a degree
        if lvl == "masters" and m.group("mast").lower().startswith("master"):
            nxt = sent[m.end():m.end() + 14].lower()
            if not re.match(r"^(?:'?s?\s*)?(?:degree|in\b|of\b|or\b|/|,|\)|\+|and\b|\s*$)", nxt) and "'" not in m.group("mast"):
                continue
        out.append((lvl, m.start(), m.end()))
    return out


_LEVEL_RANK = {"bachelors": 1, "masters": 2, "mba": 2, "phd": 3}


_ALT_BEFORE = re.compile(
    r"(?:\bor\s+|\bwith\s+)(?:an?\s+|the\s+|relevant\s+|related\s+|\w+\s+years?\s+(?:of\s+experience\s+)?(?:with\s+)?)*$", re.I)
_CRED_MENU = re.compile(r"\b(CFA|CPA|FRM|CMA|CFP|CAIA)\b")


def extract_degree(units: list[Unit]) -> tuple[DegreeFact | None, DegreeFact | None]:
    required: list[tuple[int, DegreeFact]] = []
    preferred: list[tuple[int, DegreeFact]] = []
    for u in units:
        levels = _levels_in(u.text)
        if not levels or _EDU_NOISE.search(u.text):
            continue
        sent = u.text
        inline_req = bool(_REQ_INLINE.search(sent))
        neutral_unmarked = u.section == "neutral" and not inline_req   # may only add a "preferred" mention
        req_here: list[str] = []
        for i, (lvl, a, b) in enumerate(levels):
            nxt = levels[i + 1][1] if i + 1 < len(levels) else len(sent)
            tail = sent[b:min(nxt, b + 60)]
            head = sent[max(0, a - 40):a]
            if _LEVEL_RANK[lvl] >= 2:
                has_years = bool(_RANGE.search(sent) or _SINGLE.search(sent))
                if has_years and _ALT_BEFORE.search(sent[max(0, a - 70):a]):
                    continue    # "7 years ..., or 5 years with an advanced degree": a route, not a requirement
                if _CRED_MENU.search(sent) and re.search(r"\bor\b", sent, re.I):
                    # "MBA, graduate degree, or CFA designation": a menu of credentials, not an MBA bar
                    preferred.append((_LEVEL_RANK[lvl], DegreeFact(lvl, _evidence(sent), "low")))
                    continue
            is_pref = bool(_PREF_INLINE.search(tail) or re.search(r"(preferred|ideally|desired|bonus)\W{0,3}$", head, re.I)
                           or (u.section == "preferred" and not _REQ_INLINE.search(head + tail)))
            if is_pref:
                preferred.append((_LEVEL_RANK[lvl], DegreeFact(lvl, _evidence(sent), "medium")))
            elif not neutral_unmarked:
                req_here.append(lvl)
        if not req_here:
            continue
        has_or = bool(re.search(r"\b(or|/|either)\b", sent, re.I)) and len(req_here) > 1
        equiv = bool(_EQUIV.search(sent))
        if has_or:
            pick = min(req_here, key=lambda l: _LEVEL_RANK[l])
        else:
            pick = max(req_here, key=lambda l: _LEVEL_RANK[l])
        if equiv and _LEVEL_RANK[pick] >= 2:
            # "Master's or equivalent experience": not a hard requirement
            preferred.append((_LEVEL_RANK[pick], DegreeFact(pick, _evidence(sent), "low")))
            continue
        conf = u.section_conf if u.section == "required" else "medium"
        if inline_req:
            conf = "high"
        # a degree listed with several years routes (Bachelor's + 3 or Master's + 1) is not a hard master's bar
        if len(_routes(sent)) >= 2 and len({r["degree"] for r in _routes(sent)}) >= 2:
            pick = "bachelors"
        required.append((_LEVEL_RANK[pick], DegreeFact(pick, _evidence(sent), conf)))

    deg_req = max(required, key=lambda x: (x[0], CONF_RANK[x[1].confidence]))[1] if required else None
    deg_pref = max(preferred, key=lambda x: x[0])[1] if preferred else None
    if deg_pref and deg_req and _LEVEL_RANK[deg_pref.level] <= _LEVEL_RANK[deg_req.level]:
        deg_pref = None
    return deg_req, deg_pref


# --------------------------------------------------------------------------------------------
# Certifications
# --------------------------------------------------------------------------------------------

_CERTS: list[tuple[str, re.Pattern]] = [
    ("CFA", re.compile(r"\bCFA\b|chartered financial analyst", re.I)),
    ("CPA", re.compile(r"\bCPA\b|certified public accountant", re.I)),
    ("SIE", re.compile(r"\bSIE\b|securities industry essentials", re.I)),
    ("FRM", re.compile(r"\bFRM\b|financial risk manager", re.I)),
    ("CMA", re.compile(r"\bCMA\b|certified management accountant", re.I)),
    ("CAIA", re.compile(r"\bCAIA\b", re.I)),
    ("CFP", re.compile(r"\bCFP\b|certified financial planner", re.I)),
]
_SERIES = re.compile(r"\bSeries\s+((?:\d{1,2}\s*(?:,|and|&|/|or)?\s*)+)", re.I)
_OBTAIN = re.compile(
    r"\b(obtain|obtained|obtaining|acquire|acquired|pass|passing|sit for|register|registered|complete the|"
    r"within\s+(?:\w+\s+)?(?:\(\d+\)\s+)?(?:days|months|weeks|\d+|your first|the first)|first \d+ (?:days|months)|"
    r"sponsor(?:ed|ship)?|will support|willing to (?:obtain|pursue|sit|take)|able to (?:obtain|pass)|"
    r"must be able to|upon hire|after (?:hire|joining|start)|company[- ]paid|we will (?:pay|cover)|"
    r"you will (?:be required to )?(?:obtain|sit|take|register))\b", re.I)
_CERT_PREF = re.compile(
    r"\b(preferred|a plus|nice to have|bonus|desired|advantage|candidates?|pursuing|progress(?:ing)? toward|"
    r"working toward|in progress|level (?:i|1|ii|2|iii|3) candidate|ideally)\b", re.I)
_CERT_REQ = re.compile(r"\b(required|must (?:have|hold|possess|be)|minimum|mandatory|need to (?:have|hold)|"
                       r"hold(?:s|ing)? (?:an? )?(?:active |current )?)\b", re.I)
_STATUS_RANK = {"required": 4, "obtainable": 3, "preferred": 2, "mentioned": 1}


def extract_certs(units: list[Unit]) -> list[CertFact]:
    best: dict[str, CertFact] = {}

    def record(name: str, sent: str, status: str) -> None:
        cur = best.get(name)
        if cur is None or _STATUS_RANK[status] > _STATUS_RANK[cur.status]:
            best[name] = CertFact(name, status, _evidence(sent))

    for u in units:
        found: list[str] = []
        for name, rx in _CERTS:
            if rx.search(u.text):
                found.append(name)
        for m in _SERIES.finditer(u.text):
            for num in re.findall(r"\d{1,2}", m.group(1)):
                if int(num) in (3, 4, 6, 7, 22, 24, 63, 65, 66, 79, 86, 87):
                    found.append(f"Series {int(num)}")
        if not found:
            continue
        sent = u.text
        if _OBTAIN.search(sent) and not re.search(r"\b(must (?:currently )?hold|currently hold|active)\b", sent, re.I):
            status = "obtainable"
        elif _CERT_PREF.search(sent) or u.section == "preferred":
            status = "preferred"
        elif _CERT_REQ.search(sent) or (u.section == "required" and u.section_conf in ("high", "medium")):
            status = "required"
        else:
            status = "mentioned"
        for name in found:
            record(name, sent, status)
    return sorted(best.values(), key=lambda c: c.cert)


# --------------------------------------------------------------------------------------------
# Work mode
# --------------------------------------------------------------------------------------------

_DAYS = r"(?:[1-4](?:\s*(?:-|to)\s*[1-4])?|one|two|three|four)"
_HYBRID = re.compile(
    r"\bhybrid\b|"
    rf"\b{_DAYS}\s*days? (?:per|a|each|/) ?(?:week|wk)\b.{{0,30}}\b(?:office|onsite|on-site|in[- ]person)\b|"
    rf"\b(?:in|at) (?:the |our )?office\b.{{0,25}}\b{_DAYS}\s*days?\b|"
    rf"\b{_DAYS}\s*days?\b.{{0,25}}\b(?:in|at) (?:the |our )?office\b|"
    r"\bflexible (?:work|working)\b.{0,30}\boffice\b", re.I)
_REMOTE = re.compile(
    r"\b(fully|100%|completely|entirely|100 percent)\s+remote\b|\bremote[- ](?:first|only|position|role|opportunity)\b|"
    r"\bthis (?:is a |position is |role is )(?:fully |100% )?remote\b|\bwork from anywhere\b", re.I)
_ONSITE = re.compile(
    r"\b(on[- ]?site|in[- ]office|onsite)\b|\b5 days (?:a |per )?(?:week )?(?:in|at) (?:the |our )?office\b|"
    r"\bwork(?:ing)? (?:from|in) (?:the |our )?office\b|\breport to (?:the |our )?office\b", re.I)
_NOT_REMOTE = re.compile(r"\b(not|no|isn'?t|is not|cannot|can'?t)\b[^.]{0,25}\bremote\b", re.I)


def extract_work_mode(text: str, location: str = "") -> ModeFact:
    # Location field "Remote" is a strong signal on ATS feeds.
    loc = (location or "").strip()
    sentences = [u.text for u in to_units(text)]
    for s in sentences:
        if _HYBRID.search(s):
            return ModeFact("hybrid", _evidence(s))
    if re.search(r"\bhybrid\b", loc, re.I):
        return ModeFact("hybrid", f"Location: {loc}")
    for s in sentences:
        if _NOT_REMOTE.search(s):
            continue
        if _REMOTE.search(s):
            return ModeFact("remote", _evidence(s))
    if re.match(r"^\s*(?:us[- ]|usa[- ]|united states[ ,-]+)?remote\b", loc, re.I) or loc.lower() in ("remote", "remote - us"):
        return ModeFact("remote", f"Location: {loc}")
    for s in sentences:
        if _ONSITE.search(s):
            return ModeFact("onsite", _evidence(s))
    return ModeFact("unknown")


# --------------------------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------------------------

def extract(text: str, location: str = "") -> Extraction:
    units = to_units(text or "")
    years_req, years_pref, skill_years, no_exp = extract_years(units)
    deg_req, deg_pref = extract_degree(units)
    return Extraction(
        years_required=years_req,
        years_preferred=years_pref,
        years_skill_specific=skill_years,
        degree_required=deg_req,
        degree_preferred=deg_pref,
        certs=extract_certs(units),
        work_mode=extract_work_mode(text or "", location),
        no_experience_needed=no_exp,
    )
