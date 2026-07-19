#!/usr/bin/env python3
"""Conservative personal/company email labeling.

Harvests founder/owner NAMES from a store's pages, then labels each extracted
email. Design goal: a false 'personal' is worse than a miss, so personal_confirmed
requires a strong name<->email match. Nothing is dropped here — every email is
kept and LABELED. See SECURITY_CHECKLIST.md / design spec.

Labels: personal_confirmed, personal_likely, company_role, company_softrole,
        company_generic, company_freemail_role, shared_footer
"""
import re, json, os, unicodedata

FREE_HOSTS = ("gmail.com", "googlemail.com", "yahoo.", "hotmail.", "outlook.",
              "icloud.com", "aol.com", "gmx.", "proton", "live.com", "msn.com",
              "me.com", "mail.com", "ymail.com", "zoho.com")
HARD_ROLE = {"info", "support", "service", "sales", "order", "orders", "return",
             "returns", "wholesale", "admin", "billing", "account", "accounts",
             "help", "care", "enquiries", "enquiry", "inquiries", "inquiry",
             "office", "marketing", "press", "hr", "cs", "noreply", "donotreply",
             "webmaster", "postmaster", "abuse", "newsletter", "subscribe",
             "customerservice", "customercare", "feedback", "general", "mail"}
SOFT_ROLE = {"hello", "hi", "hey", "contact", "hola", "team", "ask", "talk"}
BRAND_SUFFIX = {"shop", "store", "team", "official", "enquiries", "co", "hq", "online"}
COMMON_WORD_STOP = {"the", "and", "our", "your", "this", "that", "with", "from",
                    "team", "shop", "store", "official", "welcome", "home",
                    "about", "contact", "support", "founder", "owner", "ceo",
                    "order", "new", "best", "all", "for", "www", "here", "get",
                    "meet", "story", "brand", "company", "since", "made", "love"}
APP_VENDOR = {"starapps.studio", "gist-apps.com", "notifyboost.net", "beeketing.com",
              "simprosys.com", "giftnote.com", "glood.ai", "heliumdev.com",
              "shopify.com", "myshopify.com", "wixpress.com", "sitewit.com"}
FIRST_NAMES = None  # optional gazetteer (booster only); loaded lazily if present


def defold(s):
    """lowercase + strip diacritics (Jose->jose), for symmetric matching."""
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def _strip_digits(s):
    return re.sub(r"\d+$", "", s)


_FN = None
def _first_names():
    """Lazy-load the first-name gazetteer (booster / name-shape detector)."""
    global _FN
    if _FN is None:
        try:
            _FN = set(open(os.path.join(os.path.dirname(__file__), "first_names.txt")).read().split())
        except Exception:
            _FN = set()
    return _FN


def name_shaped(local):
    """Confidence 0-42 that a local-part looks like a personal name WITHOUT any
    page-name evidence. Conservative: only clear patterns (known first name,
    firstname+initial, initial.surname, first.last)."""
    FN = _first_names()
    lf = _strip_digits(defold(local))
    toks = [t for t in re.split(r"[._\-]", lf) if t]
    if not toks or any(t in HARD_ROLE or t in SOFT_ROLE or defold(t) in COMMON_WORD_STOP for t in toks):
        return 0
    if len(toks) == 1:
        t = toks[0]
        if t in FN:
            return 42
        if len(t) >= 4 and t[:-1] in FN:        # mattr->matt, davidd->david, suem->sue
            return 40
        return 0
    a, b = toks[0], toks[-1]
    if a in FN or b in FN:                        # sarah.chen / chen.sarah / sarah.c
        return 42
    if len(a) == 1 and len(b) >= 3:               # a.baudouin (initial.surname)
        return 36
    return 0


# ---- name harvesting -------------------------------------------------------
# Prose NAME must be Title-case (NO re.I on the capture) -> kills "JEWELLERY YOU".
_NAME = r"[A-ZÀ-Þ][a-zà-ÿ'’\-]{1,14}"
_ROLE_TRIGGER = r"(?:founder|co-founder|owner|proprietor|ceo|creator|maker|designer)"
_PAT = [
    (re.compile(r"[Ff]ounded by\s+((?:%s\s?){1,3})" % _NAME), 3),
    (re.compile(r"%s[:\-]?\s+((?:%s\s?){1,3})" % (_ROLE_TRIGGER, _NAME)), 3),
    (re.compile(r"[Mm]y name(?:'s| is)\s+((?:%s\s?){1,3})" % _NAME), 3),
    (re.compile(r"((?:%s\s?){1,3}),?\s+(?:the\s+)?%s" % (_NAME, _ROLE_TRIGGER)), 3),
    (re.compile(r"(?:I'm|I am|This is)\s+((?:%s\s?){1,3})" % _NAME), 2),
    (re.compile(r"[Mm]eet\s+((?:%s\s?){1,3})" % _NAME), 1),
]
_TOKEN_OK = re.compile(r"^[A-ZÀ-Þ][a-zà-ÿ'’\-]{1,14}$")


def _is_person_name(name, brand_tokens, strict=True):
    """A person name = 1-3 tokens, each Title-case Latin w/ a vowel, none a common
    word, and the whole thing != the brand. strict=False trusts structured data
    (JSON-LD/meta) and allows non-Latin scripts for non-Anglo founders."""
    toks = [t for t in name.split() if t]
    if not (1 <= len(toks) <= 3):
        return False
    flat = defold("".join(toks))
    if flat in brand_tokens or any(flat == b or (len(b) >= 5 and b in flat) for b in brand_tokens):
        return False
    for t in toks:
        df = defold(t)
        if df in COMMON_WORD_STOP or df in HARD_ROLE or df in SOFT_ROLE:
            return False
        if strict:
            if not _TOKEN_OK.match(t) or not re.search(r"[aeiouyà-ÿ]", df):
                return False
    return True


def harvest_names(html, brand_tokens=frozenset()):
    """Return list of (name, weight, source), highest weight first."""
    out = {}
    def add(name, w, src, strict):
        n = " ".join((name or "").split()[:3]).strip(" ,.-")
        if n and _is_person_name(n, brand_tokens, strict) and (n not in out or out[n][0] < w):
            out[n] = (w, src)
    # JSON-LD founder/author/owner (trusted; any script)
    for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.S | re.I):
        try:
            data = json.loads(block.strip())
            objs = data if isinstance(data, list) else [data]
        except Exception:
            for m in re.findall(r'"(?:founder|author)"\s*:\s*\{[^}]*?"name"\s*:\s*"([^"]{2,40})"', block):
                add(m, 3, "jsonld", False)
            continue
        for obj in objs:
            if not isinstance(obj, dict):
                continue
            for key in ("founder", "owner"):          # NOT "author" (often brand/CMS)
                v = obj.get(key)
                if isinstance(v, dict) and v.get("name"):
                    add(v["name"], 3, "jsonld", False)
                elif isinstance(v, str):
                    add(v, 3, "jsonld", False)
    # prose on stripped text (strict Title-case)
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))[:200000]
    for pat, w in _PAT:
        for m in pat.findall(text):
            add(m if isinstance(m, str) else m[0], w, "prose", True)
    return sorted(([n, w, s] for n, (w, s) in out.items()), key=lambda x: -x[1])


def brand_tokens(html, domain):
    """Brand identity tokens (root + site name) to exclude from person names."""
    toks = {defold(domain.replace("www.", "").split(".")[0])}
    for pat in (r'og:site_name["\'][^>]+content=["\']([^"\']{2,40})',
                r'<title[^>]*>([^<]{2,60})</title>'):
        m = re.search(pat, html, re.I)
        if m:
            toks.add(re.sub(r"[^a-z0-9]", "", defold(m.group(1).split("|")[0].split("-")[0])))
    return frozenset(t for t in toks if t)


# ---- name <-> local-part variants -----------------------------------------
def name_variants(name):
    parts = [defold(p) for p in name.split() if len(defold(p)) >= 2]
    parts = [p for p in parts if p not in COMMON_WORD_STOP]
    v = set()
    if not parts:
        return v
    if len(parts) == 1:
        v.add(parts[0])
    else:
        for a, b in ((parts[0], parts[-1]), (parts[-1], parts[0])):  # both orders (CJK)
            v |= {a, a + b, a + "." + b, a[0] + b, a + b[0], a[0] + "." + b}
        v.add("".join(parts))
    return {x for x in v if len(x) >= 2}


# ---- classification --------------------------------------------------------
def classify(email, domain, page_text_fold, names, cross_domain_count=1):
    """Return dict(label, confidence, matched_name, name_source)."""
    email = email.lower().strip()
    if "@" not in email:
        return _r("company_generic", 0)
    local, host = email.split("@", 1)
    root = domain.replace("www.", "").split(".")[0].lower()
    ltoks = [t for t in re.split(r"[._\-]", _strip_digits(local)) if t]
    is_free = any(f in host for f in FREE_HOSTS)
    is_dm = bool(root) and root in host
    host_ok = is_dm or is_free
    lfold = defold(_strip_digits(local))

    # shared footer / app-vendor (forced non-personal)
    if cross_domain_count >= 3 or any(v in host for v in APP_VENDOR):
        return _r("shared_footer", 0)

    # GUARD A/B: hard role token anywhere -> company_role, STOP
    if any(t in HARD_ROLE for t in ltoks):
        return _r("company_freemail_role" if is_free else "company_role", 0)

    # HOST GATE: if the email is on neither the store's own domain nor a freemail,
    # it's almost always a third-party (font vendor, app, agency) -> never personal.
    if not host_ok:
        return _r("other_domain", 0)

    # GUARD: brand handle (local contains brand root, or ends in brand suffix)
    flat = re.sub(r"[._\-]", "", local)
    if root and len(root) >= 4 and root in flat:
        return _r("company_generic", 0)
    if ltoks and ltoks[-1] in BRAND_SUFFIX and len(ltoks) > 1:
        return _r("company_generic", 0)

    # name <-> local match
    best = None  # (score, name, source, kind)
    for name, weight, source in names:
        vs = name_variants(name)
        nfold = defold(name)
        full = (lfold in vs) or any(defold(p) == lfold for p in name.split() if len(p) >= 3)
        if not full:
            continue
        adjacent = page_text_fold and (lfold in page_text_fold) and _near(page_text_fold, email, nfold)
        kind = "full" if lfold in {defold(p) for p in name.split()} else "variant"
        score = 0
        score += {3: 45, 2: 32, 1: 20}.get(weight, 15)      # source strength
        score += 20 if is_dm else 0                            # own-domain bonus
        score += 15 if kind == "full" else 5
        score += 12 if adjacent else 0
        if is_free and not adjacent:
            score = min(score, 45)                             # free-mail needs adjacency for confirm
        if best is None or score > best[0]:
            best = (score, name, source, kind)

    # eponymous domain: a strong name == domain root (audrey==audreyleighton...)
    eponymous = None
    for name, weight, source in names:
        if weight >= 3:
            nf = re.sub(r"[^a-z]", "", defold(name))
            if nf and (nf in root or (len(root) >= 5 and root in nf)):
                eponymous = (name, source)
                break

    is_soft = any(t in SOFT_ROLE for t in ltoks)

    # SOFT role (hello@/contact@): default company_softrole; upgrade to personal_likely
    # ONLY on the store's own domain with an eponymous single founder.
    if is_soft and not best:
        if is_dm and eponymous:
            return _r("personal_likely", 40, eponymous[0], "eponymous")
        return _r("company_softrole", 0)

    if best:
        score = best[0]
        if score >= 60:
            return _r("personal_confirmed", score, best[1], best[2])
        if score >= 35:
            return _r("personal_likely", score, best[1], best[2])

    # name-in-domain positive signal: eponymous founder store, own-domain primary contact
    if eponymous and is_dm:
        return _r("personal_likely", 38, eponymous[0], "eponymous")

    # name-shaped local-part w/o page evidence (mattr@, a.baudouin@, sarah.chen@)
    ns = name_shaped(local)
    if ns >= 35:
        return _r("personal_likely", ns, "", "name-shaped")

    return _r("company_generic", 0)   # kept, company campaign (incl. solo-founder gmail)


def _near(text, email, name_fold, window=120):
    i = text.find(email.split("@")[0])
    if i < 0:
        return False
    seg = text[max(0, i - window): i + window]
    return name_fold.split()[0] in seg if name_fold else False


def _r(label, conf, name="", source=""):
    return {"label": label, "confidence": int(conf), "matched_name": name, "name_source": source}
