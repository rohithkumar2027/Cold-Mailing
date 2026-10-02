"""
Recruiter Scraper & Lead Sourcing Module
========================================
Finds LinkedIn profiles of TECH recruiters (SWE, AI/ML, data, campus/early-careers)
who CURRENTLY work at a given company, using search-engine dorking via ddgs.

What changed vs. the first version (why results were incomplete / wrong):

RECALL (find more people)
  * ~30-45 query variants per company instead of 5-6: every recruiter title
    x role keyword x Indian city, plus early-careers / campus / sourcer terms.
  * Each query is sent to several search backends (auto, bing, brave, google,
    duckduckgo) and the results are merged, with retries + backoff.
  * De-duplication is by LinkedIn profile slug only (the old code also dropped
    different people who happen to share a name).
  * Company aliases (Meta = Facebook, Amazon = AWS, TCS = Tata Consultancy
    Services, ...) so profiles that write the legal/brand variant are not lost.

PRECISION (drop people who are not current recruiters of that company)
  * Whole-word matching only. The old check used substring tests, so "hr"
    matched "through"/"three" and "meta" matched "metadata".
  * Uses LinkedIn's own result format: the headline/company part of the title
    and the FIRST entry of "Experience:" in the snippet (the current job).
  * Rejects "Ex-Google", "Former ...", "Previously at ..." and profiles whose
    headline/experience names a different company.
  * Every lead is labelled Verified or Likely and ranked; tech recruiters
    (technical / engineering / campus / AI-ML) come first. Non-tech recruiters
    (sales, finance, legal, ...) and non-recruiting HR roles are dropped.
"""
import re
import time
import random
import urllib.parse
import logging
from typing import List, Dict, Optional, Tuple

try:
    from ddgs import DDGS
except ImportError:
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        DDGS = None

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

INDIA_KEYWORDS = [
    "india", "bengaluru", "bangalore", "hyderabad", "gurgaon", "gurugram",
    "mumbai", "pune", "delhi", "noida", "chennai", "karnataka", "telangana",
    "haryana", "maharashtra", "kolkata", "ahmedabad", "kochi", "jaipur",
    "chandigarh", "coimbatore", "thiruvananthapuram", "trivandrum", "indore",
    "navi mumbai", "uttar pradesh", "tamil nadu", "west bengal", "kerala",
]

INDIA_CITIES = [
    "Bengaluru", "Bangalore", "Hyderabad", "Pune", "Gurugram", "Gurgaon",
    "Mumbai", "Chennai", "Noida", "Delhi", "Kolkata", "Ahmedabad",
]

# Kept for backwards compatibility with anything importing it.
RECRUITER_KEYWORDS = [
    "recruiter", "recruiting", "recruitment", "talent acquisition",
    "talent partner", "talent specialist", "talent lead", "talent sourcer",
    "sourcer", "early careers", "campus recruiter", "university recruiter",
    "university relations", "ta partner", "talent advisor",
    "technical recruiter", "tech recruiter", "engineering recruiter",
]

# A profile must look like a recruiter through one of these (whole-word regex).
_RECRUITER_RE = re.compile(
    r"(?<![a-z])(?:"
    r"recruit\w*|talent\s+acqui\w*|talent\s+(?:partner|advis[oe]r|specialist|lead|scout|sourc\w*|manager|consultant|associate|business\s+partner)|"
    r"(?:sr\.?\s+|senior\s+)?sourcer|sourcing\s+(?:specialist|lead|partner|manager)|"
    r"ta\s+(?:partner|lead|specialist|manager|consultant|associate)|"
    r"head\s+of\s+(?:talent|recruit\w*)|"
    r"early\s+(?:careers?|talent)|university\s+(?:relations|hiring|programs?)|campus\s+(?:hiring|relations|programs?)|"
    r"college\s+(?:hiring|relations)|hiring\s+(?:partner|lead|specialist|team)|"
    r"tech\s+hiring|engineering\s+hiring"
    r")(?![a-z])",
    re.IGNORECASE,
)

# Only these count when we have to fall back to the free-text snippet.
_STRONG_RECRUITER_RE = re.compile(
    r"(?<![a-z])(?:recruit\w*|talent\s+acqui\w*|sourcer|talent\s+partner|talent\s+scout)(?![a-z])",
    re.IGNORECASE,
)

# HR / People roles that are NOT recruiting, and recruiters for non-tech functions.
_NON_RECRUITING_RE = re.compile(
    r"hr\s*business\s*partner|\bhrbp\b|payroll|compensation|benefits\b|learning\s+(?:and|&)\s+development|"
    r"\bl&d\b|talent\s+(?:management|development|engagement)|employee\s+(?:relations|experience|engagement)|"
    r"hr\s+operations|people\s+analytics|chief\s+human|\bchro\b|hr\s+generalist|"
    r"software\s+engineer|developer\b|data\s+scientist",
    re.IGNORECASE,
)
_NON_TECH_FUNCTION_RE = re.compile(
    r"non[-\s]?tech\w*|sales\s+recruit\w*|marketing\s+recruit\w*|finance\s+recruit\w*|legal\s+recruit\w*|"
    r"(?:g&a|corporate|business|retail|store|warehouse|operations|hardware\s+manufacturing|healthcare|nurse\w*)\s+recruit\w*|"
    r"sales\s+(?:hiring|talent)|executive\s+search",
    re.IGNORECASE,
)

_TECH_RE = re.compile(
    r"(?<![a-z])(?:tech\w*|engineer\w*|software|sde|swe|developers?|ai|ml|gen\s?ai|machine\s+learning|deep\s+learning|"
    r"llm|nlp|data|research|scien\w+|cloud|product|campus|university|college|early\s+(?:careers?|talent)|"
    r"graduates?|interns?\w*|students?|new\s+grads?|it|cyber\w*|security|platform|devops|sre|infra\w*)(?![a-z])",
    re.IGNORECASE,
)

ROLE_TERMS = {
    "swe": ["software engineer", "software engineering", "SDE", "engineering", "developer", "backend", "full stack"],
    "ai_ml": ["AI", "machine learning", "ML", "data science", "GenAI", "LLM", "research scientist", "applied science"],
    "data_science": ["data science", "data scientist", "analytics", "machine learning", "data engineering", "AI"],
}

# Recruiter titles used to build queries. Ordered by how likely they are to hit.
CORE_TITLES = [
    "technical recruiter", "talent acquisition", "recruiter", "campus recruiter",
    "university recruiter", "early careers", "talent partner", "senior recruiter",
    "engineering recruiter", "talent acquisition partner", "sourcer",
    "talent acquisition specialist", "recruiting lead", "talent acquisition lead",
    "technical sourcer", "tech hiring", "campus hiring", "university relations",
    "recruitment consultant", "talent advisor",
]

ROLE_TITLES = {
    "swe": ["software engineering recruiter", "SDE recruiter", "engineering talent acquisition", "tech recruiter"],
    "ai_ml": ["AI recruiter", "ML recruiter", "machine learning recruiter", "research recruiter",
              "AI talent acquisition", "data science recruiter"],
    "data_science": ["data science recruiter", "analytics recruiter", "data talent acquisition", "AI recruiter"],
}

# Brand / legal-name variants of well known companies.
KNOWN_ALIASES = {
    "google": ["google", "google india", "google cloud", "alphabet"],
    "meta": ["meta", "facebook", "meta platforms"],
    "facebook": ["meta", "facebook", "meta platforms"],
    "amazon": ["amazon", "amazon web services", "aws", "amazon india", "amazon development centre", "amazon development center"],
    "aws": ["aws", "amazon web services", "amazon"],
    "microsoft": ["microsoft", "microsoft india", "microsoft corporation"],
    "apple": ["apple", "apple india"],
    "tcs": ["tcs", "tata consultancy services"],
    "tata consultancy services": ["tcs", "tata consultancy services"],
    "infosys": ["infosys", "infosys limited"],
    "wipro": ["wipro", "wipro limited", "wipro technologies"],
    "hcl": ["hcl", "hcltech", "hcl technologies"],
    "hcltech": ["hcl", "hcltech", "hcl technologies"],
    "ibm": ["ibm", "ibm india", "ibm research"],
    "sap": ["sap", "sap labs", "sap labs india"],
    "nvidia": ["nvidia", "nvidia india"],
    "jpmorgan": ["jpmorgan", "jpmorgan chase", "j.p. morgan", "jp morgan"],
    "goldman sachs": ["goldman sachs", "goldman"],
    "morgan stanley": ["morgan stanley"],
    "walmart": ["walmart", "walmart global tech", "walmart global tech india", "walmart labs"],
    "flipkart": ["flipkart", "flipkart internet"],
    "phonepe": ["phonepe", "phone pe"],
    "cred": ["cred", "dreamplug"],
    "adobe": ["adobe", "adobe india"],
    "oracle": ["oracle", "oracle india", "oracle cloud"],
    "salesforce": ["salesforce", "salesforce india"],
    "openai": ["openai"],
    "tiktok": ["tiktok", "bytedance"],
    "bytedance": ["tiktok", "bytedance"],
}

_COMPANY_SUFFIX_RE = re.compile(
    r"\b(?:inc\.?|incorporated|ltd\.?|limited|pvt\.?|private|llc|llp|corp\.?|corporation|co\.?|company|technologies|technology|india|group)\b",
    re.IGNORECASE,
)

# Search backends to try (ddgs). Unknown backends just raise and are skipped.
SEARCH_BACKENDS = ["auto", "bing", "brave", "google", "duckduckgo"]


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #

def company_aliases(company: str) -> List[str]:
    """All lowercase spellings of a company we should accept as 'the same company'."""
    base = company.strip().lower()
    aliases = {base}
    stripped = re.sub(r"\s+", " ", _COMPANY_SUFFIX_RE.sub(" ", base)).strip(" ,.-")
    if len(stripped) >= 2:
        aliases.add(stripped)
    for key in (base, stripped):
        for a in KNOWN_ALIASES.get(key, []):
            aliases.add(a)
    return sorted((a for a in aliases if a), key=len, reverse=True)


def _alias_regex(aliases: List[str]) -> "re.Pattern":
    """Whole-word regex for any alias (so 'meta' never matches 'metadata')."""
    body = "|".join(re.escape(a) for a in aliases)
    return re.compile(rf"(?<![a-z0-9]){'(?:' + body + ')'}(?![a-z0-9])", re.IGNORECASE)


def is_in_india(url: str, snippet: str, title: str) -> bool:
    """Checks if the LinkedIn profile is located in India."""
    if "in.linkedin.com" in url.lower():
        return True
    combined = f"{title} {snippet}".lower()
    return any(re.search(rf"(?<![a-z]){re.escape(loc)}(?![a-z])", combined) for loc in INDIA_KEYWORDS)


def clean_linkedin_url(url: str) -> str:
    """Normalizes a LinkedIn profile URL to https://www.linkedin.com/in/<slug>."""
    if not url:
        return ""

    if "duckduckgo.com/l/?uddg=" in url:
        parsed = urllib.parse.urlparse(url)
        params = urllib.parse.parse_qs(parsed.query)
        if "uddg" in params:
            url = params["uddg"][0]

    m = re.search(r"linkedin\.com/in/([^/?#\s]+)", url, re.IGNORECASE)
    if not m:
        return url.split("?")[0].rstrip("/")
    slug = urllib.parse.unquote(m.group(1)).strip().lower()
    return f"https://www.linkedin.com/in/{slug}"


def _profile_slug(url: str) -> str:
    m = re.search(r"/in/([^/?#]+)", url)
    return m.group(1).lower() if m else url.lower()


def _split_title(raw_title: str) -> List[str]:
    """Splits a LinkedIn result title into its parts: [name, headline..., company]."""
    # Carousel results: "Name - Role ... Other Person - Role" -> keep the first profile.
    first = re.split(r"\.{2,}\s*[A-Z]|…\s*[A-Z]", raw_title)[0]
    first = re.sub(r"\s*[-|–—]\s*LinkedIn.*$", "", first, flags=re.IGNORECASE)
    first = re.sub(r"\s*\|\s*(?:Join|Professional).*$", "", first, flags=re.IGNORECASE)
    return [p.strip() for p in re.split(r"\s+[-–—|]\s+", first) if p.strip()]


def _experience_companies(snippet: str) -> List[str]:
    """LinkedIn snippets contain 'Experience: A · B · C' (most recent first)."""
    m = re.search(r"Experience:\s*([^\n]+?)(?:\s*[·•|]\s*(?:Education|Location)\s*:|\n|$)", snippet, re.IGNORECASE)
    if not m:
        return []
    return [c.strip() for c in re.split(r"\s*[·•]\s*", m.group(1)) if c.strip()]


# --------------------------------------------------------------------------- #
# Query building
# --------------------------------------------------------------------------- #

def build_search_queries(
    company: str, role_category: str = "swe", location: str = "India", depth: str = "deep"
) -> List[str]:
    """
    Builds many query variants. Search engines return only ~10-30 results per query,
    so coverage comes from varying the title / role keyword / city.

    depth: "fast" (~12 queries), "deep" (~40 queries, default).
    """
    comp = company.strip()
    is_in = location.lower() == "india"
    prefix = "site:in.linkedin.com/in" if is_in else "site:linkedin.com/in"
    role_titles = ROLE_TITLES.get(role_category, ROLE_TITLES["swe"])
    role_terms = ROLE_TERMS.get(role_category, ROLE_TERMS["swe"])

    queries: List[str] = []

    def add(q: str) -> None:
        if q not in queries:
            queries.append(q)

    # 1) Role-specific titles first (highest precision for the chosen track)
    for t in role_titles:
        add(f'{prefix} "{comp}" "{t}"')

    # 2) Generic recruiter titles
    for t in CORE_TITLES:
        add(f'{prefix} "{comp}" "{t}"')

    # 3) Recruiter title x role keyword ("technical recruiter" + "machine learning", ...)
    for term in role_terms:
        add(f'{prefix} "{comp}" "recruiter" "{term}"')
        add(f'{prefix} "{comp}" "talent acquisition" "{term}"')

    fast = [
        q for q in queries[:6]
    ] + [
        f'{prefix} "{comp}" "campus recruiter" OR "university recruiter" OR "early careers"',
        f'{prefix} "{comp}" "talent acquisition" OR "recruiter" OR "sourcer"',
    ]
    if is_in:
        fast += [f'{prefix} "{comp}" recruiter {c}' for c in ("Bengaluru", "Hyderabad", "Pune", "Gurugram")]
    if depth == "fast":
        return list(dict.fromkeys(fast))

    # 4) City sweeps (India): different cities surface different people
    if is_in:
        for city in INDIA_CITIES:
            add(f'{prefix} "{comp}" "recruiter" "{city}"')
            add(f'{prefix} "{comp}" "talent acquisition" "{city}"')
            add(f'{prefix} "{comp}" "technical recruiter" "{city}"')

    # 5) Free-text (no site: operator) - catches profiles the site: search hides
    add(f'linkedin.com/in "{comp}" recruiter {location}')
    add(f'"{comp}" "technical recruiter" {location} linkedin')
    add(f'"{comp}" "talent acquisition" {location} linkedin profile')
    add(f'"{comp}" "campus recruiter" OR "university recruiter" {location} linkedin')

    # Put the "fast" set first so early-exit still gets the best queries.
    ordered = list(dict.fromkeys(fast + queries))
    return ordered


def build_search_query(company: str, role_category: str = "swe") -> str:
    """Returns the primary search query for backwards compatibility."""
    queries = build_search_queries(company, role_category, location="India", depth="fast")
    for q in queries:  # prefer the classic "technical recruiter" query when the track has one
        if "technical recruiter" in q.lower():
            return q
    return queries[0] if queries else ""


# --------------------------------------------------------------------------- #
# Parsing & verification
# --------------------------------------------------------------------------- #

def parse_linkedin_title(raw_title: str, target_company: str, snippet: str = "") -> Dict[str, str]:
    """Extracts the person's full name and role title from a LinkedIn result title."""
    aliases = company_aliases(target_company)
    alias_re = _alias_regex(aliases)
    parts = _split_title(raw_title)

    name = ""
    title = ""

    if len(parts) >= 2:
        name = parts[0]
        role_parts = []
        for p in parts[1:]:
            # a segment that is just the company (or "<company> India") is not a role
            if alias_re.fullmatch(p.strip()) or p.strip().lower() == "india":
                continue
            p_clean = re.split(r"\s+(?:at|@)\s+", p, maxsplit=1, flags=re.IGNORECASE)[0].strip()
            if p_clean and not alias_re.fullmatch(p_clean):
                role_parts.append(p_clean)
        if role_parts:
            title = " - ".join(role_parts)
    elif len(parts) == 1:
        match = re.match(r"^([^,]+?)\s+(?:at|,)\s+(.+)$", parts[0], re.IGNORECASE)
        if match:
            name, title = match.group(1), match.group(2)
        else:
            name = parts[0]

    title = title.rstrip("-|–— ").strip()

    # Fall back to the snippet when the title carried no role
    if not title or len(title) < 3:
        m = _RECRUITER_RE.search(snippet or "")
        if m:
            start = max(0, m.start() - 20)
            frag = snippet[start : m.end() + 30]
            title = re.split(r"[.,;|\n·]", frag[m.start() - start :])[0].strip()

    # Clean name: keep letters (any script), spaces, dots, apostrophes and hyphens.
    name = re.sub(r"[\(\[\{].*?[\)\]\}]", "", name)
    name = re.sub(r"[^\w\s\.\'-]|[\d_]", "", name, flags=re.UNICODE).strip()
    # drop credentials / honorifics
    name = re.sub(r"\b(?:mba|phd|cpa|shrm[-\s]?\w*|pmp|dr|mr|mrs|ms)\b\.?", "", name, flags=re.IGNORECASE).strip()
    name = re.sub(r"\s{2,}", " ", name)

    # Recruiter words that leaked into the name field
    role_words = {"recruiter", "recruiting", "talent", "hiring", "lead", "senior", "director", "manager", "acquisition"}
    words = name.split()
    if any(w.lower() in role_words for w in words):
        clean = [w for w in words if w.lower() not in role_words]
        if len(clean) >= 2:
            name = " ".join(clean[:3])

    return {
        "name": name.strip(),
        "title": title.strip() if title.strip() else "Recruiter",
        "company": target_company,
    }


def build_about(title: str, snippet: str, company: str) -> str:
    """
    A short 'about the recruiter' line built ONLY from facts present in the search result
    (headline, location, education, earlier employers, and any bio text). Nothing is invented.
    """
    snip = re.sub(r"\s+", " ", snippet or "").strip()
    alias_re = _alias_regex(company_aliases(company))
    role = (title or "").strip() or "Recruiter"
    parts = [f"{role} at {company}."]

    m = re.search(r"Location:\s*([^·|]+?)(?=\s*(?:[·|]|\d+\+?\s*connections|$))", snip)
    if m and m.group(1).strip():
        parts.append(f"Based in {m.group(1).strip()}.")

    prior = [c for c in _experience_companies(snip) if not alias_re.search(c)][:3]
    if prior:
        parts.append("Earlier experience: " + ", ".join(prior) + ".")

    m = re.search(r"Education:\s*([^·|]+?)(?=\s*(?:[·|]|Location:|$))", snip)
    if m and m.group(1).strip():
        parts.append(f"Education: {m.group(1).strip()}.")

    # free-text bio: whatever is left after removing the structured bits and LinkedIn boilerplate
    rest = re.sub(r"(Experience|Education|Location):\s*[^·|]+", " ", snip)
    rest = re.sub(r"View\s+.{0,60}?profile on LinkedIn[^.]*\.?", " ", rest, flags=re.IGNORECASE)
    rest = re.sub(r"\d+\+?\s*connections?[^.]*\.?|[·|]", " ", rest)
    rest = re.sub(r"\s+", " ", rest).strip(" .")
    if len(rest) >= 40:
        parts.append(rest[:200].rstrip() + ("…" if len(rest) > 200 else ""))
    return " ".join(parts)


_OPEN_TO_WORK_RE = re.compile(
    r"open\s+to\s+work|#?opentowork|immediate\s+joiner|actively\s+(?:looking|seeking)|"
    r"seeking\s+(?:new\s+)?(?:opportunit\w+|roles?|jobs?)|looking\s+for\s+(?:a\s+)?(?:new\s+)?(?:opportunit\w+|job|role|change)|"
    r"career\s+break|between\s+jobs|freelanc\w+",
    re.IGNORECASE,
)
# Agency / vendor recruiters who only mention the company as a client.
_AGENCY_RE = re.compile(
    r"clients?\b|staffing|\brpo\b|consultancy|consulting\s+firm|vendor|on\s+behalf|outsourc\w+|"
    r"hiring\s+for|recruit\w*\s+for|partnered\s+with|placed\s+candidates",
    re.IGNORECASE,
)
_AT_COMPANY_RE = re.compile(r"(?:(?<![a-z])at\s+|@\s*)([^|,·•]+)", re.IGNORECASE)


def own_snippet(title: str, snippet: str) -> str:
    """
    Search engines sometimes attach another person's text to a result
    ("View Mohit Bansal's profile ..." under Catherine's link). Such a snippet says
    nothing about this person, so it is discarded.
    """
    snippet = snippet or ""
    parts = _split_title(title or "")
    if not parts:
        return snippet
    name_words = {w for w in re.findall(r"[a-z]+", parts[0].lower()) if len(w) > 1}
    m = re.search(r"View\s+(.{2,60}?)[’'`]s\s+profile", snippet, re.IGNORECASE)
    if m and name_words:
        other = set(re.findall(r"[a-z]+", m.group(1).lower()))
        if other and not (other & name_words):
            return ""
    return snippet


def evaluate_profile(title: str, snippet: str, company: str) -> Dict:
    """
    Decide whether a search hit is a CURRENT recruiter at `company`.

    Verified = the person's own headline says "<role> at <company>" (or the company is
               its own headline segment), or LinkedIn's "Experience:" lists the company
               first - and nothing contradicts it.
    Likely   = weaker evidence (company only mentioned in passing, or headline and
               Experience disagree).

    Returns {"ok": bool, "confidence": "Verified"|"Likely"|"", "tech": bool,
             "score": int, "reason": str}
    """
    aliases = company_aliases(company)
    alias_re = _alias_regex(aliases)
    snippet = own_snippet(title, snippet)
    parts = _split_title(title)
    headline_parts = parts[1:] if len(parts) > 1 else []
    headline = " | ".join(headline_parts)
    head_low = headline.lower()
    snip_low = snippet.lower()

    def reject(reason: str) -> Dict:
        return {"ok": False, "confidence": "", "tech": False, "score": 0, "reason": reason}

    # ---- 1. former employee / not employed markers (headline OR snippet) ----------
    former_re = re.compile(
        r"(?:\bformer(?:ly)?\b|\bpreviously\b|\bpast\b|\bearlier\b|\balumn\w*|\bworked\s+(?:at|with|for)\b)"
        r"[\s:\-–/@]*(?:\w+\s+){0,4}?" + alias_re.pattern,
        re.IGNORECASE,
    )
    ex_prefix_re = re.compile(rf"(?<![a-z0-9])ex[\s:\-–/@]*{alias_re.pattern}", re.IGNORECASE)
    for text, where in ((headline, "headline"), (snippet, "snippet")):
        if former_re.search(text) or ex_prefix_re.search(text):
            return reject(f"former employee ({where})")
    if _OPEN_TO_WORK_RE.search(headline) or _OPEN_TO_WORK_RE.search(snippet):
        return reject("open to work / not currently employed")

    # ---- 2. where does the evidence say the person works NOW? ----------------------
    head_current = False      # "... at <company>" or a segment that is the company
    head_other = False        # "... at <some other company>"
    for seg in headline_parts:
        if alias_re.match(seg.strip()):
            head_current = True
        for m in _AT_COMPANY_RE.finditer(seg):
            if alias_re.match(m.group(1).strip()):
                head_current = True
            else:
                head_other = True
    head_mention = bool(alias_re.search(headline))

    if not head_current and not head_other and not head_mention and len(headline_parts) >= 2:
        # Name - Headline - Company : last segment is another employer
        last = headline_parts[-1].lower()
        looks_like_place = any(loc in last for loc in INDIA_KEYWORDS)
        if not _RECRUITER_RE.search(last) and not _TECH_RE.search(last) and not looks_like_place:
            head_other = True

    exp = _experience_companies(snippet)
    exp_first_target = bool(exp) and bool(alias_re.match(exp[0].strip()))
    exp_first_other = bool(exp) and not exp_first_target
    agency = bool(_AGENCY_RE.search(headline) or _AGENCY_RE.search(snippet))

    if head_other and not head_current:
        return reject("headline names another company")
    if exp_first_other and not head_current:
        return reject("current experience is another company")

    if head_current and not exp_first_other:
        confidence = "Verified"
    elif exp_first_target and not agency:
        confidence = "Verified"
    elif head_current:
        confidence = "Likely"  # headline says target, but Experience lists another company first
    elif agency:
        return reject("agency recruiter - company is only a client")
    elif head_mention:
        confidence = "Likely"
    else:
        at_re = re.compile(rf"(?:\bat\b|@)\s*{alias_re.pattern}", re.IGNORECASE)
        if at_re.search(snippet):
            confidence = "Likely"
        else:
            return reject("company not shown as current employer")

    # ---- 3. must be a recruiter --------------------------------------------------------
    role_in_head = bool(_RECRUITER_RE.search(headline))
    role_in_snip = bool(_STRONG_RECRUITER_RE.search(snippet))
    if not (role_in_head or role_in_snip):
        return reject("not a recruiter")
    if not role_in_head and _NON_RECRUITING_RE.search(headline):
        return reject("non-recruiting role")
    if role_in_head and re.search(r"hr\s*business\s*partner|\bhrbp\b|payroll|compensation", head_low):
        return reject("non-recruiting HR role")
    if _NON_TECH_FUNCTION_RE.search(headline):
        return reject("non-tech recruiter")

    # ---- 4. tech relevance + score ------------------------------------------------------
    tech = bool(_TECH_RE.search(headline)) or bool(re.search(r"tech|engineer|software|\bai\b|\bml\b|campus|university", snip_low))
    score = 100 if confidence == "Verified" else 60
    score += 20 if tech else 0
    score += 10 if role_in_head else 0
    score += 5 if exp_first_target else 0
    return {"ok": True, "confidence": confidence, "tech": tech, "score": score, "reason": "ok"}


def is_currently_at_company(title: str, snippet: str, company: str) -> bool:
    """Backwards-compatible boolean wrapper around evaluate_profile()."""
    return evaluate_profile(title, snippet, company)["ok"]


# --------------------------------------------------------------------------- #
# Searching
# --------------------------------------------------------------------------- #

def _search_web(query: str, want: int = 30) -> List[Dict]:
    """
    Run one query against several backends, merge by URL.
    Retries with backoff; a backend that errors is skipped.
    """
    if DDGS is None:
        return []
    merged: List[Dict] = []
    seen = set()
    ok_backends = 0
    for backend in SEARCH_BACKENDS:
        items = None
        for attempt in range(2):
            try:
                ddg = DDGS(timeout=15)
                try:
                    items = list(ddg.text(query, max_results=want, backend=backend))
                except TypeError:  # older library without `backend`
                    items = list(ddg.text(query, max_results=want))
                break
            except Exception as e:  # rate limit / unknown backend / network
                logger.debug(f"search '{query}' backend={backend} attempt={attempt}: {e}")
                time.sleep(0.8 * (attempt + 1) + random.random() * 0.5)
        if items is None:
            continue
        if items:
            ok_backends += 1
        for it in items:
            href = it.get("href", "")
            if href and href not in seen:
                seen.add(href)
                merged.append(it)
        if ok_backends >= 2 or len(merged) >= want:
            break
    return merged


def find_recruiters_for_company(
    company: str,
    role_category: str = "swe",
    max_results: int = 10,
    location: str = "India",
    depth: str = "deep",
    tech_only: bool = False,
    search_fn=None,
    verified_only: bool = False,
) -> List[Dict]:
    """
    Discover recruiters currently working at `company`.

    max_results : how many leads to return; 0 / None = return everything found.
    depth       : "fast" (~12 queries) or "deep" (~40 queries, finds many more people).
    verified_only : keep only people whose own headline / current experience names the company.
    tech_only   : drop recruiters whose headline shows no tech / campus / AI-ML focus.
    search_fn   : injectable search function (query -> list of {href,title,body}); used by tests.
    """
    company = company.strip()
    search = search_fn or _search_web
    if not company or (search_fn is None and not DDGS):
        return []

    unlimited = not max_results or max_results <= 0
    queries = build_search_queries(company, role_category, location=location, depth=depth)

    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed

    lock = threading.Lock()
    found: Dict[str, Dict] = {}  # slug -> lead
    stop = threading.Event()
    target_good = None if unlimited else max(max_results * 2, 10)

    def run_query(query: str) -> None:
        if stop.is_set():
            return
        try:
            items = search(query)
        except Exception as e:
            logger.warning(f"Query error on '{query}': {e}")
            return
        for item in items:
            clean_url = clean_linkedin_url(item.get("href", ""))
            if "linkedin.com/in/" not in clean_url:
                continue
            raw_title = item.get("title", "") or ""
            # drop snippet text that belongs to a different person
            raw_snippet = own_snippet(raw_title, item.get("body", "") or "")

            verdict = evaluate_profile(raw_title, raw_snippet, company)
            if not verdict["ok"]:
                continue
            if tech_only and not verdict["tech"]:
                continue
            if verified_only and verdict["confidence"] != "Verified":
                continue
            if location.lower() == "india" and not is_in_india(item.get("href", ""), raw_snippet, raw_title):
                continue

            parsed = parse_linkedin_title(raw_title, company, snippet=raw_snippet)
            name = parsed["name"]
            words = name.split()
            if len(words) < 2 or len(words) > 5:
                continue
            if any(w.lower() in {"linkedin", "profiles", "jobs", "directory", "hiring", "recruiter"} for w in words):
                continue

            lead = {
                "name": name,
                "title": parsed["title"],
                "company": company,
                "location": "India" if location.lower() == "india" else location,
                "employment_status": f"{verdict['confidence']}: currently at {company}",
                "linkedin_url": clean_url,
                "snippet": raw_snippet,
                "about": build_about(parsed["title"], raw_snippet, company),
                "role_category": role_category,
                "match_confidence": verdict["confidence"],
                "tech_focus": verdict["tech"],
                "_score": verdict["score"],
            }
            slug = _profile_slug(clean_url)
            with lock:
                prev = found.get(slug)
                if prev is None or lead["_score"] > prev["_score"]:
                    found[slug] = lead

        with lock:
            good = sum(1 for l in found.values() if l["match_confidence"] == "Verified" and l["tech_focus"])
            if target_good is not None and good >= target_good:
                stop.set()
        time.sleep(random.uniform(0.3, 0.9))  # be gentle with search engines

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(run_query, q) for q in queries]
        for f in as_completed(futures):
            try:
                f.result()
            except Exception as e:
                logger.warning(f"Future error: {e}")
            if stop.is_set():
                for other in futures:
                    other.cancel()

    leads = sorted(found.values(), key=lambda l: (-l["_score"], l["name"].lower()))
    for l in leads:
        l.pop("_score", None)
    return leads if unlimited else leads[:max_results]
