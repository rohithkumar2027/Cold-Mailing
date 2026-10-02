import re
import json
import time
import hashlib
import threading
from pathlib import Path
import unicodedata
import logging
import urllib.parse
from collections import Counter
from typing import Dict, List, Optional, Tuple

try:
    import dns.resolver
    DNS_AVAILABLE = True
except ImportError:
    dns = None
    DNS_AVAILABLE = False

try:
    from ddgs import DDGS
except ImportError:
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        DDGS = None

import requests

from config.settings import (
    KNOWN_COMPANY_PATTERNS,
    CONTACTOUT_API_KEY,
    APOLLO_API_KEY,
    CONTACTOUT_API_KEYS,
    APOLLO_API_KEYS,
    parse_keys,
    CONTACTOUT_CREDITS_PER_KEY,
    CONTACTOUT_CREDITS_PER_EMAIL,
    API_KEY_STORE,
)

logger = logging.getLogger(__name__)


def normalize_string(text):
    normalized = unicodedata.normalize('NFKD', text).encode('ASCII', 'ignore').decode('utf-8')
    return re.sub(r'[^a-zA-Z0-9\s]', '', normalized).strip().lower()


def clean_person_name(raw_name):
    if not raw_name:
        return ('', '')
    name = re.sub(r'[\(\[\{].*?[\)\]\}]', '', raw_name)
    suffixes = [
        r',\s*phr\b', r',\s*sphr\b', r',\s*shrm-cp\b', r',\s*shrm-scp\b',
        r',\s*mba\b', r',\s*pmp\b', r',\s*cpc\b', r',\s*cir\b', r',\s*cdr\b',
    ]
    for s in suffixes:
        name = re.sub(s, '', name, flags=re.IGNORECASE)
    for sep in [' - ', ' | ', chr(8211)+' ', chr(8212)+' ', ' : ']:
        if sep in name:
            name = name.split(sep)[0]
    name = re.sub(r"[^a-zA-Z\s\.\'-]", '', name).strip()
    parts = [p.strip(" .'-") for p in name.split() if p.strip(" .'-")]
    honorifics = {'mr', 'mrs', 'ms', 'dr', 'prof'}
    if parts and parts[0].lower() in honorifics:
        parts = parts[1:]
    if not parts:
        return ('', '')
    elif len(parts) == 1:
        return (parts[0].lower(), '')
    else:
        return (parts[0].lower(), parts[-1].lower())


def resolve_company_domain(company_name):
    if not company_name:
        return ('', None)
    cleaned_company = normalize_string(company_name)
    for key, data in KNOWN_COMPANY_PATTERNS.items():
        if key == cleaned_company or key in cleaned_company or cleaned_company in key:
            return (data['domain'], data.get('pattern'))
    suffixes_to_remove = [' inc', ' llc', ' corp', ' corporation', ' technologies', ' technology', ' labs', ' group', ' co']
    domain_candidate = cleaned_company
    for s in suffixes_to_remove:
        if domain_candidate.endswith(s):
            domain_candidate = domain_candidate[: -len(s)].strip()
    clean_domain = re.sub(r'\s+', '', domain_candidate) + '.com'
    return (clean_domain, None)


def verify_mx_records(domain):
    if not DNS_AVAILABLE or dns is None or not domain:
        return True
    try:
        answers = dns.resolver.resolve(domain, 'MX', lifetime=3.0)
        return len(answers) > 0
    except Exception:
        return False


def generate_email_permutations(first_name, last_name, domain):
    if not domain or not first_name:
        return []
    f = first_name[0]
    l = last_name[0] if last_name else ''
    patterns = []
    if last_name:
        patterns.append((f'{first_name}.{last_name}@{domain}', 'first.last'))
        patterns.append((f'{f}{last_name}@{domain}', 'flast'))
        patterns.append((f'{first_name}@{domain}', 'first'))
        patterns.append((f'{first_name}_{last_name}@{domain}', 'first_last'))
        patterns.append((f'{first_name}{last_name}@{domain}', 'firstlast'))
        patterns.append((f'{first_name}{l}@{domain}', 'firstl'))
        patterns.append((f'{last_name}.{first_name}@{domain}', 'last.first'))
        patterns.append((f'{last_name}@{domain}', 'last'))
    else:
        patterns.append((f'{first_name}@{domain}', 'first'))
        patterns.append((f'{first_name}.recruiting@{domain}', 'first.recruiting'))
    return patterns


def key_id(key):
    """Short, non-reversible id used to refer to a key without exposing it."""
    return hashlib.sha1(key.encode('utf-8')).hexdigest()[:8]


def mask_key(key):
    return '••••' + key[-4:] if len(key) > 8 else '••••'


class KeyPool:
    """
    API keys used IN ORDER, with optional credit accounting.

    * A key is used until its credits are gone, then the next key takes over (5 keys x 100 credits at
      10 credits per email = 50 emails, then it stops).
    * Credits are reserved before a lookup and given back if nothing was found, so parallel lookups can
      never overspend a key, and a lookup that finds nothing costs nothing.
    * A key the service reports as out of credits / invalid / rate-limited is set aside.
    * Keys added in the app (and the credits each has used) are saved to a local file.
    """

    def __init__(self, keys=None, name='api', credits_per_key=None, cost_per_use=0, store=None):
        self.name = name
        self.credits_per_key = credits_per_key      # None = credits not tracked
        self.cost = cost_per_use or 0
        self._env = list(keys or [])
        self._added = []
        self._used = {}
        self._blocked_until = {}
        self._notes = {}          # key -> last message from the service (why it was set aside)
        self._store = Path(store) if store else None
        self._lock = threading.RLock()
        self._load()

    # ---- persistence -------------------------------------------------------------------
    def _load(self):
        if not self._store or not self._store.exists():
            return
        try:
            data = json.loads(self._store.read_text(encoding='utf-8'))
            self._added = [k for k in data.get('added', []) if isinstance(k, str) and k]
            # Files written by older versions counted keys as 'used up' whenever the service refused them,
            # even for reasons that had nothing to do with credits. Start those counters fresh.
            if data.get('v', 1) >= 2:
                self._used = {str(k): int(v) for k, v in data.get('used', {}).items()}
        except Exception as e:
            logger.warning(f'{self.name}: could not read key store: {e}')

    def _save(self):
        if not self._store:
            return
        try:
            self._store.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._store.with_suffix('.tmp')
            tmp.write_text(json.dumps({'v': 2, 'added': self._added, 'used': self._used}, indent=2), encoding='utf-8')
            tmp.replace(self._store)
        except Exception as e:
            logger.warning(f'{self.name}: could not save key store: {e}')

    # ---- keys --------------------------------------------------------------------------
    @property
    def keys(self):
        with self._lock:
            return list(dict.fromkeys(self._env + self._added))

    def set_keys(self, keys):
        with self._lock:
            self._env, self._added, self._used, self._blocked_until, self._notes = list(keys or []), [], {}, {}, {}

    def add_key(self, key):
        key = (key or '').strip().strip('"\'')
        if len(key) < 8 or re.search(r'\s', key):
            return False
        with self._lock:
            if key in self.keys:
                return False
            self._added.append(key)
            self._save()
        return True

    def remove(self, kid):
        with self._lock:
            for k in list(self._added):
                if key_id(k) == kid:
                    self._added.remove(k)
                    self._used.pop(kid, None)
                    self._blocked_until.pop(k, None)
                    self._save()
                    return True
        return False

    def reset(self, kid):
        """Forget the credits counted as used (e.g. the plan renewed)."""
        with self._lock:
            found = any(key_id(k) == kid for k in self.keys)
            if found:
                self._used.pop(kid, None)
                for k in self.keys:
                    if key_id(k) == kid:
                        self._blocked_until.pop(k, None)
                        self._notes.pop(k, None)
                self._save()
            return found

    # ---- credits -----------------------------------------------------------------------
    def remaining(self, key):
        if self.credits_per_key is None:
            return None
        return max(0, self.credits_per_key - self._used.get(key_id(key), 0))

    def _usable(self, key, now):
        if self._blocked_until.get(key, 0) > now:
            return False
        rem = self.remaining(key)
        return rem is None or rem >= max(self.cost, 1)

    def next_key(self, exclude=()):
        """First usable key, without reserving credits."""
        now = time.time()
        with self._lock:
            for k in self.keys:
                if k not in exclude and self._usable(k, now):
                    return k
        return None

    def reserve(self, exclude=()):
        """First usable key, with the credits for one lookup already set aside."""
        now = time.time()
        with self._lock:
            for k in self.keys:
                if k in exclude or not self._usable(k, now):
                    continue
                if self.credits_per_key is not None:
                    kid = key_id(k)
                    self._used[kid] = self._used.get(kid, 0) + self.cost
                    self._save()
                return k
        return None

    def refund(self, key):
        if self.credits_per_key is None:
            return
        with self._lock:
            kid = key_id(key)
            self._used[kid] = max(0, self._used.get(kid, 0) - self.cost)
            self._save()

    def mark_exhausted(self, key):
        """The service says this key has no credits: count them all as used."""
        if self.credits_per_key is None:
            return
        with self._lock:
            self._used[key_id(key)] = self.credits_per_key
            self._save()

    def note(self, key, text):
        with self._lock:
            if text:
                self._notes[key] = text
            else:
                self._notes.pop(key, None)

    def unblock(self, key):
        with self._lock:
            self._blocked_until.pop(key, None)
            self._notes.pop(key, None)

    def block(self, key, seconds, reason=''):
        with self._lock:
            self._blocked_until[key] = time.time() + seconds
            if reason:
                self._notes[key] = reason
        logger.warning(f'{self.name}: key ...{key[-4:]} set aside for {int(seconds)}s ({reason})')

    # ---- reporting (never includes a full key) -------------------------------------------
    def snapshot(self):
        now = time.time()
        with self._lock:
            rows = []
            for k in self.keys:
                rem = self.remaining(k)
                rows.append({
                    'id': key_id(k),
                    'masked': mask_key(k),
                    'source': 'app' if k in self._added and k not in self._env else '.env',
                    'used': self._used.get(key_id(k), 0),
                    'remaining': rem,
                    'usable': self._usable(k, now),
                    'note': self._notes.get(k, ''),
                })
            return rows

    def status(self):
        rows = self.snapshot()
        left = sum(r['remaining'] or 0 for r in rows) if self.credits_per_key is not None else None
        emails = sum((r['remaining'] or 0) // max(self.cost, 1) for r in rows) if self.credits_per_key is not None else None
        return {
            'total': len(rows),
            'active': sum(1 for r in rows if r['usable']),
            'credits_left': left,
            'emails_left': emails,
        }


CONTACTOUT_POOL = KeyPool(CONTACTOUT_API_KEYS, 'ContactOut', credits_per_key=CONTACTOUT_CREDITS_PER_KEY,
                          cost_per_use=CONTACTOUT_CREDITS_PER_EMAIL, store=API_KEY_STORE)
APOLLO_POOL = KeyPool(APOLLO_API_KEYS, 'Apollo')

CONTACTOUT_URL = 'https://api.contactout.com/v1/people/linkedin'
APOLLO_URL = 'https://api.apollo.io/v1/people/match'
_EXHAUSTED_SECONDS = 6 * 3600   # out of credits / invalid key: don't retry for a few hours
_RATE_LIMIT_SECONDS = 65        # rate limited: try again after about a minute
_API_CACHE = {}                 # linkedin url / name -> result, so re-running never spends a credit twice
_API_CACHE_LOCK = threading.Lock()


def get_key_status():
    """How many keys are loaded / usable and how many credits are left (never the keys themselves)."""
    return {'contactout': CONTACTOUT_POOL.status(), 'apollo': APOLLO_POOL.status()}


def _api_status_code(resp):
    """The HTTP status, or the status_code ContactOut also puts inside its JSON body."""
    code = resp.status_code
    if code == 200:
        try:
            body_code = resp.json().get('status_code')
            if isinstance(body_code, int) and body_code != 200:
                return body_code
        except Exception:
            pass
    return code


def _first_real_email(values):
    for v in values or []:
        v = str(v).strip()
        if '@' in v and 'not_unlocked' not in v.lower() and 'example.com' not in v.lower():
            return v
    return ''


def _contactout_parse(data):
    profile = data.get('profile', data) if isinstance(data, dict) else {}
    if not isinstance(profile, dict):
        return None
    work = profile.get('work_email') or profile.get('work_emails') or []
    generic = profile.get('email') or profile.get('emails') or []
    personal = profile.get('personal_email') or profile.get('personal_emails') or []
    ordered = list(work) + [e for e in generic if e not in work] + [e for e in personal if e not in work and e not in generic]
    primary = _first_real_email(ordered)
    if not primary:
        return None
    phones = profile.get('phone') or profile.get('phone_numbers') or []
    return {
        'primary_email': primary,
        'phone_number': phones[0] if phones else '',
        'alternative_emails': [e for e in ordered if e != primary][:4],
        'confidence': 'Verified (ContactOut)',
    }


def _contactout_headers(key):
    # ContactOut's examples send the token plus 'authorization: basic'; sending both is harmless if only one is needed.
    return {'token': key, 'authorization': 'basic', 'Accept': 'application/json', 'Content-Type': 'application/json'}


def _api_message(resp):
    """A short, human-readable reason from an error response."""
    try:
        body = resp.json()
        if isinstance(body, dict):
            for k in ('message', 'error', 'detail', 'msg'):
                if body.get(k):
                    return str(body[k])[:160]
    except Exception:
        pass
    return (resp.text or '').strip().replace('\n', ' ')[:160]


def _looks_like_no_credits(text):
    t = (text or '').lower()
    return any(w in t for w in ('credit', 'quota', 'insufficient', 'exceeded your', 'usage limit', 'payment'))


def enrich_with_contactout(linkedin_url, api_key=None, errors=None):
    """
    Looks the LinkedIn profile up on ContactOut, spending credits key by key: the first key with enough
    credits is used; when it runs out (or the service refuses it) the next key is used. A lookup that
    finds no email is refunded. Returns None when nothing was found or every key is used up.
    Any reason the service gave for refusing a key is appended to `errors` (if a list is passed).
    """
    if not linkedin_url:
        return None
    errors = errors if errors is not None else []
    with _API_CACHE_LOCK:
        if ('co', linkedin_url) in _API_CACHE:
            return _API_CACHE[('co', linkedin_url)]
    explicit = parse_keys(api_key) if api_key else []
    pool = KeyPool(explicit, 'ContactOut') if explicit else CONTACTOUT_POOL
    tried = set()
    for _ in range(max(1, len(pool.keys))):
        key = pool.reserve(exclude=tried)
        if not key:
            break
        tried.add(key)
        try:
            resp = requests.get(CONTACTOUT_URL, params={'profile': linkedin_url},
                                headers=_contactout_headers(key), timeout=15.0)
        except requests.RequestException as e:
            logger.warning(f'ContactOut network error: {e}')
            errors.append(f'ContactOut could not be reached ({type(e).__name__})')
            pool.refund(key)
            continue
        code = _api_status_code(resp)
        if code in (401, 402, 403):
            msg = _api_message(resp)
            pool.refund(key)
            reason = f'ContactOut refused key ...{key[-4:]}: HTTP {code}' + (f' - {msg}' if msg else '')
            logger.warning(reason)
            errors.append(reason)
            if code == 402 or _looks_like_no_credits(msg):
                pool.mark_exhausted(key)                      # the service says the credits are gone
                pool.block(key, _EXHAUSTED_SECONDS, reason)
            else:
                # 401/403 usually means the key is not an API key (or API access is not enabled for it).
                # That is not "out of credits", so do not count its credits as used.
                pool.block(key, 300, reason)
            continue
        if code == 429:
            pool.refund(key)
            pool.block(key, _RATE_LIMIT_SECONDS, 'rate limited (HTTP 429)')
            errors.append('ContactOut rate limit reached, try again in a minute')
            continue
        result = None
        if code == 200:
            try:
                result = _contactout_parse(resp.json())
            except Exception:
                result = None
        elif code != 404:
            errors.append(f'ContactOut answered HTTP {code}' + (f' - {_api_message(resp)}' if _api_message(resp) else ''))
        if result is None:
            pool.refund(key)           # nothing found -> no credits spent
        with _API_CACHE_LOCK:
            if code in (200, 404):     # only remember real answers, never errors
                _API_CACHE[('co', linkedin_url)] = result
        return result
    return None


def probe_contactout(key):
    """
    Checks a key WITHOUT spending credits: asks about a profile that does not exist. A key the service accepts
    answers 404 (not found); a key it refuses answers 401/403. Returns {ok, http, message}.
    """
    try:
        resp = requests.get(CONTACTOUT_URL, params={'profile': 'https://www.linkedin.com/in/zz-no-such-person-0000000'},
                            headers=_contactout_headers(key), timeout=15.0)
    except requests.RequestException as e:
        return {'ok': False, 'http': 0, 'message': f'Could not reach ContactOut ({type(e).__name__}). Check your internet connection.'}
    code = _api_status_code(resp)
    msg = _api_message(resp)
    if code in (200, 404):
        return {'ok': True, 'http': code, 'message': 'Key accepted by ContactOut.'}
    if code == 429:
        return {'ok': True, 'http': code, 'message': 'Key accepted, but ContactOut is rate limiting right now.'}
    if code == 401:
        return {'ok': False, 'http': code, 'message': 'ContactOut says this token is invalid (HTTP 401). ' + msg}
    if code == 403:
        return {'ok': False, 'http': code, 'message': 'ContactOut refused this token (HTTP 403) - API access is probably not enabled for it. ' + msg}
    if code == 402:
        return {'ok': False, 'http': code, 'message': 'ContactOut says there are no API credits left on this key (HTTP 402). ' + msg}
    return {'ok': False, 'http': code, 'message': f'Unexpected answer from ContactOut (HTTP {code}). {msg}'}


def enrich_with_apollo(name, company, linkedin_url='', api_key=None):
    """Same idea for Apollo.io: several keys, automatic switch when one is used up."""
    ck = ('ap', linkedin_url or f'{name}|{company}')
    with _API_CACHE_LOCK:
        if ck in _API_CACHE:
            return _API_CACHE[ck]
    explicit = parse_keys(api_key) if api_key else []
    pool = KeyPool(explicit, 'Apollo') if explicit else APOLLO_POOL
    payload = {'name': name, 'organization_name': company}
    if linkedin_url:
        payload['linkedin_url'] = linkedin_url
    tried = set()
    for _ in range(max(1, len(pool.keys))):
        key = pool.next_key(exclude=tried)
        if not key:
            break
        tried.add(key)
        try:
            resp = requests.post(APOLLO_URL, json=payload, timeout=12.0,
                                 headers={'Content-Type': 'application/json', 'Cache-Control': 'no-cache', 'X-Api-Key': key})
        except requests.RequestException as e:
            logger.warning(f'Apollo network error: {e}')
            continue
        if resp.status_code in (401, 402, 403):
            pool.block(key, _EXHAUSTED_SECONDS, f'HTTP {resp.status_code}: out of credits or invalid key')
            continue
        if resp.status_code == 429:
            pool.block(key, _RATE_LIMIT_SECONDS, 'rate limited')
            continue
        result = None
        if resp.status_code == 200:
            try:
                person = resp.json().get('person') or {}
                email = _first_real_email([person.get('email')])   # skips Apollo's locked placeholders
                phones = person.get('phone_numbers') or []
                if email:
                    result = {'primary_email': email,
                              'phone_number': phones[0].get('sanitized_number', '') if phones else '',
                              'alternative_emails': [], 'confidence': 'Verified (Apollo)'}
            except Exception:
                result = None
        with _API_CACHE_LOCK:
            _API_CACHE[ck] = result
        return result
    return None


GENERIC_EMAIL_PREFIXES = [
    'support@', 'help@', 'jobs@', 'careers@', 'press@', 'media@',
    'info@', 'contact@', 'feedback@', 'noreply@', 'no-reply@',
    'recruitment@', 'hr@', 'admin@', 'team@', 'hello@',
]

EMAIL_REGEX = re.compile(r'\b[a-zA-Z0-9_.+-]{2,}@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b')


def _normalize_for_email(text):
    """Replace bracketed obfuscation like [at] and [dot] — but NOT plain 'at' words."""
    text = re.sub(r'\s*\[at\]\s*|\s*\(at\)\s*', '@', text, flags=re.IGNORECASE)
    text = re.sub(r'\s*\[dot\]\s*|\s*\(dot\)\s*', '.', text, flags=re.IGNORECASE)
    return text


def _deobfuscate_spaced_at(text):
    """
    Handle 'name at domain.com' style obfuscation.
    Only applies when the pattern looks like: word at word.tld (without an existing @).
    Runs SEPARATELY from _normalize_for_email to avoid corrupting real emails.
    """
    # Match: local-part AT domain.tld (no existing @)
    return re.sub(
        r'(?<![^\s])([a-zA-Z0-9][a-zA-Z0-9._-]+)\s+at\s+([a-zA-Z0-9][a-zA-Z0-9.-]+\.[a-zA-Z]{2,})(?![^\s])',
        r'\1@\2',
        text,
        flags=re.IGNORECASE,
    )


def _extract_emails_from_text(text, domain_filter=None):
    """
    Extract valid, non-generic email addresses from text.
    Runs regex on original text first, then on de-obfuscated variants.
    """
    results_set = []

    def _scan(t):
        found = EMAIL_REGEX.findall(t)
        for e in found:
            e_low = e.lower()
            if any(e_low.startswith(p) for p in GENERIC_EMAIL_PREFIXES):
                continue
            local_part = e_low.split('@')[0]
            if len(local_part) < 3:
                continue
            if domain_filter and domain_filter.lower() not in e_low:
                continue
            if e_low not in results_set:
                results_set.append(e_low)

    # 1. Scan the original text (real emails like john@example.com)
    _scan(text)
    # 2. Scan after removing [at]/[dot] obfuscation
    _scan(_normalize_for_email(text))
    # 3. Scan after replacing 'word at domain.tld' patterns
    _scan(_deobfuscate_spaced_at(text))

    return results_set



def _fetch_page_text(url, timeout=6.0):
    if not url or 'linkedin.com' in url.lower():
        return ''
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
        }
        resp = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        if resp.status_code == 200:
            text = re.sub(r'<[^>]+>', ' ', resp.text)
            text = re.sub(r'\s+', ' ', text)
            return text[:40000]
    except Exception:
        pass
    return ''


# --------------------------------------------------------------------------- #
# Keyless discovery helpers (no API keys anywhere in this section)
# --------------------------------------------------------------------------- #

_BLOCKED_DOMAINS = {
    'linkedin.com', 'wikipedia.org', 'facebook.com', 'twitter.com', 'x.com', 'instagram.com', 'youtube.com',
    'glassdoor.com', 'glassdoor.co.in', 'indeed.com', 'naukri.com', 'crunchbase.com', 'ambitionbox.com',
    'zoominfo.com', 'rocketreach.co', 'github.com', 'medium.com', 'quora.com', 'reddit.com', 'bloomberg.com',
    'forbes.com', 'economictimes.com', 'wikidata.org', 'apple.com.cn',
}
_CACHE_LOCK = threading.Lock()
_DOMAIN_CACHE: Dict[str, str] = {}
_PATTERN_CACHE: Dict[str, Optional[str]] = {}
_MX_CACHE: Dict[str, bool] = {}


def _search(query, max_results=8):
    """Web search that tries several backends; returns [] on any failure."""
    if DDGS is None:
        return []
    for backend in ('auto', 'duckduckgo', 'brave', 'bing'):
        try:
            ddg = DDGS(timeout=10)
            try:
                res = list(ddg.text(query, max_results=max_results, backend=backend))
            except TypeError:
                res = list(ddg.text(query, max_results=max_results))
            if res:
                return res
        except Exception as e:
            logger.debug(f'search {query!r} backend={backend}: {e}')
    return []


def _registrable_domain(host):
    host = (host or '').lower().split(':')[0]
    host = re.sub(r'^www\.', '', host)
    parts = host.split('.')
    if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in ('co', 'com', 'org', 'net', 'ac', 'gov'):
        return '.'.join(parts[-3:])
    return '.'.join(parts[-2:]) if len(parts) >= 2 else host


def _mx_cached(domain):
    with _CACHE_LOCK:
        if domain in _MX_CACHE:
            return _MX_CACHE[domain]
    ok = verify_mx_records(domain)
    with _CACHE_LOCK:
        _MX_CACHE[domain] = ok
    return ok


def discover_company_domain(company):
    """Finds a company's official website domain from a plain web search."""
    key = normalize_string(company)
    with _CACHE_LOCK:
        if key in _DOMAIN_CACHE:
            return _DOMAIN_CACHE[key]
    token = re.sub(r'\s+', '', key)
    first_word = key.split()[0] if key.split() else token
    found = ''
    for r in _search(f'{company} official website', max_results=8):
        dom = _registrable_domain(urllib.parse.urlparse(r.get('href', '')).netloc)
        if not dom or dom in _BLOCKED_DOMAINS:
            continue
        label = dom.split('.')[0]
        if token in dom.replace('-', '') or (len(first_word) >= 4 and first_word in label):
            found = dom
            break
    with _CACHE_LOCK:
        _DOMAIN_CACHE[key] = found
    return found


def resolve_domain_smart(company):
    """
    Returns (domain, known_pattern).
    Known-company table first; otherwise guess <name>.com and confirm it has mail (MX) records;
    if it does not, look up the real website through a web search.
    """
    domain, pattern = resolve_company_domain(company)
    if pattern:
        return domain, pattern
    if domain and _mx_cached(domain):
        return domain, None
    discovered = discover_company_domain(company)
    if discovered and _mx_cached(discovered):
        return discovered, None
    return domain, None


def learn_company_pattern(domain, company=''):
    """
    Learns how a company formats work emails (first.last, flast, ...) from public web results:
    finds '<name> ... someone@domain' mentions and checks which format explains them.
    """
    if not domain:
        return None
    with _CACHE_LOCK:
        if domain in _PATTERN_CACHE:
            return _PATTERN_CACHE[domain]
    votes = Counter()
    queries = [
        f'"@{domain}" recruiter',
        f'"@{domain}" "talent acquisition"',
        f'"@{domain}" "technical recruiter" contact',
        f'"@{domain}" "campus recruiter" OR "university recruiter"',
    ]
    for q in queries:
        for r in _search(q, max_results=10):
            text = f"{r.get('title', '')} {r.get('body', '')}"
            emails = _extract_emails_from_text(text, domain_filter='@' + domain)
            if not emails:
                continue
            names = re.findall(r"\b([A-Z][a-z]{1,15})\s+([A-Z][a-z]{1,20})\b", text)
            for e in emails:
                for fn, ln in names:
                    for cand, pat in generate_email_permutations(fn.lower(), ln.lower(), domain):
                        if cand == e:
                            votes[pat] += 1
        if sum(votes.values()) >= 3:
            break
    best = votes.most_common(1)[0][0] if votes else None
    with _CACHE_LOCK:
        _PATTERN_CACHE[domain] = best
    return best


def _matches_person(email, first, last):
    """
    True only when the address's local part is one of the normal ways to write THIS person's name
    (first.last, flast, first_last, firstlast, first, ...) - optionally followed by digits.
    A stray address on the same page (a teammate's, a shared mailbox) does not pass.
    """
    if not first:
        return False
    local = re.sub(r'\d+$', '', email.split('@')[0].lower())
    candidates = {p_.split('@')[0] for p_, _ in generate_email_permutations(first, last, 'x.com')}
    if last:
        candidates |= {f'{first}-{last}', f'{last}{first}', f'{first[0]}.{last}', f'{first}.{last[0]}'}
    return local in candidates


def _on_domain(email, domain):
    host = email.split('@')[-1].lower()
    d = domain.lower().replace('www.', '')
    return host == d or host.endswith('.' + d)


def scrape_email_from_web(name, company, domain, linkedin_url='', deadline=None, max_pages=4):
    """Looks for an email this person has PUBLISHED somewhere on the public web."""
    if DDGS is None:
        return None, None
    clean_dom = domain.lower().replace('www.', '')
    name_parts = name.strip().split()
    first_name = name_parts[0].lower() if name_parts else ''
    last_name = name_parts[-1].lower() if len(name_parts) > 1 else ''
    queries = [
        f'"{name}" "@{clean_dom}"',
        f'"{name}" {company} recruiter email',
        f'"{name}" {company} contact email',
        f'site:github.com "{name}" {company}',
    ]
    pages = 0
    for query in queries:
        if deadline and time.monotonic() > deadline:
            break
        for r in _search(query, max_results=5):
            snippet_text = f"{r.get('title', '')} {r.get('body', '')}"
            found = _extract_emails_from_text(snippet_text, domain_filter=clean_dom)
            found = [e for e in found if _on_domain(e, clean_dom) and _matches_person(e, first_name, last_name)]
            if found and first_name in snippet_text.lower():
                return found[0], 'Published (public web)'
            page_url = r.get('href', '')
            if not page_url or pages >= max_pages or (deadline and time.monotonic() > deadline):
                continue
            pages += 1
            page_text = _fetch_page_text(page_url, timeout=5.0)
            if not page_text:
                continue
            low = page_text.lower()
            has_name = first_name in low and (not last_name or last_name in low)
            found = [e for e in _extract_emails_from_text(page_text, domain_filter=clean_dom)
                     if _on_domain(e, clean_dom) and _matches_person(e, first_name, last_name)]
            if found and has_name:
                return found[0], 'Published (public page)'
    return None, None


def extract_real_published_email(name, company, domain, initial_snippet='', deadline=None):
    clean_dom = domain.lower().replace('www.', '')
    first_name, last_name = clean_person_name(name)
    if initial_snippet:
        found = [e for e in _extract_emails_from_text(initial_snippet, domain_filter=clean_dom)
                 if _on_domain(e, clean_dom) and _matches_person(e, first_name, last_name)]
        if found:
            return found[0], 'Published (LinkedIn)'
        any_emails = [e for e in _extract_emails_from_text(initial_snippet) if _matches_person(e, first_name, last_name)]
        if any_emails:
            return any_emails[0], 'Published (LinkedIn)'
    return scrape_email_from_web(name, company, domain, deadline=deadline)


def _result(full_name, email, conf, domain, mx, alts=None, phone=''):
    parts = full_name.split()
    return {
        'first_name': parts[0] if parts else '',
        'last_name': parts[-1] if len(parts) > 1 else '',
        'primary_email': email,
        'phone_number': phone,
        'alternative_emails': alts or [],
        'domain': domain,
        'confidence': conf,
        'mx_valid': mx,
    }


def find_recruiter_email(full_name, company_name, linkedin_url='', snippet='', custom_domain=None,
                         contactout_key=None, apollo_key=None, time_budget=25.0, use_paid=True):
    """
    Returns a recruiter's email ONLY if a real one was found published on the public web.
    No pattern guessing: when nothing is found, primary_email is "Not Found".

    Sources, in order: the LinkedIn search snippet, public pages that mention the person with an address
    that is a recognisable form of their name, then ContactOut and Apollo (only if keys are configured;
    several keys rotate automatically when one runs out).
    """
    deadline = time.monotonic() + time_budget
    if custom_domain:
        domain = custom_domain.strip().lower()
    else:
        domain, _ = resolve_domain_smart(company_name)

    email, conf = extract_real_published_email(full_name, company_name, domain, initial_snippet=snippet, deadline=deadline)
    if email:
        return _result(full_name, email, conf, domain, True)

    # Paid lookups run only when at least one key is configured (via .env) or passed in.
    if not use_paid:
        return _result(full_name, 'Not Found', 'None', domain, False)

    problems = []
    if linkedin_url and (contactout_key or CONTACTOUT_POOL.keys):
        co = enrich_with_contactout(linkedin_url, api_key=contactout_key, errors=problems)
        if not problems and not co and not (contactout_key or CONTACTOUT_POOL.next_key()):
            problems.append('All ContactOut keys are used up or paused')
        if co and co.get('primary_email'):
            return _result(full_name, co['primary_email'], co['confidence'], co['primary_email'].split('@')[-1],
                           True, co.get('alternative_emails', []), co.get('phone_number', ''))
    if apollo_key or APOLLO_POOL.keys:
        ap = enrich_with_apollo(full_name, company_name, linkedin_url=linkedin_url, api_key=apollo_key)
        if ap and ap.get('primary_email'):
            return _result(full_name, ap['primary_email'], ap['confidence'], ap['primary_email'].split('@')[-1],
                           True, ap.get('alternative_emails', []), ap.get('phone_number', ''))

    res = _result(full_name, 'Not Found', 'None', domain, False)
    if problems:
        res['note'] = problems[-1]     # why the paid lookup could not run (shown on the card)
    return res
