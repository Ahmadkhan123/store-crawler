#!/usr/bin/env python3
"""Free email health/hygiene layer — runs BEFORE ListClean to avoid paying for
dead addresses. Repair (never drop fixable), strict syntax, disposable filter,
and per-DOMAIN MX lookup (cached). Definitive-dead => drop; timeout => keep+flag.
"""
import re, os, csv, urllib.parse
try:
    import dns.resolver
    _HAVE_DNS = True
except Exception:
    _HAVE_DNS = False

BAD = ("sentry", "wixpress", "example.com", "cdn.", "@2x", "@3x", ".png", ".jpg",
       ".jpeg", ".gif", ".svg", ".webp", ".css", ".js", "test@", "user@", "your@",
       "yourdomain", "yourstore", "@example", "@sentry", "xxx@", "@company.com",
       "@newsletter.com", "@yourwebsite", "domain.com", "email.com", "@e-mail.com",
       # common placeholder/template person emails
       "jean.dupont", "john.doe", "jane.doe", "max.mustermann", "mustermann",
       "firstname", "lastname", "name@", "youremail", "someone@", "@domain",
       "abc@", "@sample", "@test.")
PLATFORM_INFRA = ("myshopify.com", "shopify.com", "wixpress.com")
_EMAIL = re.compile(r"[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}$")

def _load(fn):
    p = os.path.join(os.path.dirname(__file__), fn)
    return set(l.strip().lower() for l in open(p)) if os.path.exists(p) else set()

_DISPOSABLE = None
def disposable_set():
    global _DISPOSABLE
    if _DISPOSABLE is None:
        _DISPOSABLE = _load("disposable_domains.txt")
    return _DISPOSABLE


def normalize_repair(raw):
    """Idempotent repair to a fixed point. Returns cleaned email or ''."""
    e = (raw or "").strip().lower()
    for _ in range(6):
        prev = e
        e = e.strip().strip('"\'<> \t')
        if e.startswith("mailto:"):
            e = e[7:]
        e = e.split("?")[0].split("#")[0]
        e = urllib.parse.unquote(e)          # %40->@, %20/%0a->space
        e = re.sub(r"^(?:\\?u00[0-9a-f]{2})+", "", e)   # mangled > / < crumbs
        e = re.sub(r"(?:\\?u00[0-9a-f]{2})+$", "", e)
        e = e.strip().strip('.,;:/\\|()[]{}<> \t\n\r')
        e = re.sub(r"/+$", "", e)
        if e == prev:
            break
    return e


def syntax_ok(email):
    if email.count("@") != 1 or len(email) > 254:
        return False
    if not _EMAIL.match(email):
        return False
    loc, dom = email.split("@")
    if not loc or ".." in loc or loc[0] == "." or loc[-1] == ".":
        return False
    if any(b in email for b in BAD):
        return False
    tld = dom.rsplit(".", 1)[-1]
    if tld.isdigit() or not (2 <= len(tld) <= 24) or not tld.isalpha():
        return False
    return True


def static_verdict(email):
    """No-network checks. Returns (decision, reason, flags)."""
    flags = []
    dom = email.split("@")[1]
    if any(p == dom or dom.endswith("." + p) for p in PLATFORM_INFRA):
        return "drop", "platform_infra", flags
    if dom in disposable_set():
        return "drop", "disposable", flags
    return "keep", "", flags


def mx_check(domain, cache):
    """Per-domain MX lookup (cached). Returns (mx_valid, reason, flag).
    mx_valid True=has mail route, False=confirmed dead, None=unknown(keep+retry)."""
    if domain in cache:
        return cache[domain]
    if not _HAVE_DNS:
        res = (None, "no_dns_lib", "mx_unknown")
        cache[domain] = res
        return res
    r = dns.resolver.Resolver()
    r.nameservers = ["1.1.1.1", "8.8.8.8", "9.9.9.9"]
    r.timeout, r.lifetime = 5, 8
    try:
        ans = r.resolve(domain, "MX")
        res = (True, "mx", "") if len(ans) else (None, "empty_mx", "mx_unknown")
    except dns.resolver.NXDOMAIN:
        res = (False, "nxdomain", "")
    except dns.resolver.NoAnswer:
        try:                                  # RFC5321: A record = implicit MX
            r.resolve(domain, "A")
            res = (True, "implicit_a", "mx_implicit_a")
        except dns.resolver.NoAnswer:
            res = (False, "no_mx_no_a", "")
        except Exception:
            res = (None, "a_error", "mx_unknown")
    except Exception:
        res = (None, "dns_error", "mx_unknown")   # timeout/servfail -> keep+retry
    cache[domain] = res
    return res


def load_mx_cache(path):
    c = {}
    if os.path.exists(path):
        for row in csv.reader(open(path)):
            if len(row) >= 4 and row[0] != "domain":
                c[row[0]] = (None if row[1] == "" else row[1] == "True", row[2], row[3])
    return c


def save_mx_cache(path, cache):
    with open(path, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["domain", "mx_valid", "reason", "flag"])
        for d, (v, reason, flag) in cache.items():
            w.writerow([d, "" if v is None else v, reason, flag])


def health(email, mx_cache):
    """Full free-hygiene verdict for one email. Returns dict."""
    norm = normalize_repair(email)
    if not syntax_ok(norm):
        return {"email_norm": norm, "decision": "drop", "drop_reason": "bad_syntax",
                "mx_valid": "", "flags": ""}
    dec, reason, flags = static_verdict(norm)
    if dec == "drop":
        return {"email_norm": norm, "decision": "drop", "drop_reason": reason,
                "mx_valid": "", "flags": "|".join(flags)}
    dom = norm.split("@")[1]
    mx_valid, mx_reason, mx_flag = mx_check(dom, mx_cache)
    if mx_flag:
        flags.append(mx_flag)
    if mx_valid is False:
        return {"email_norm": norm, "decision": "drop", "drop_reason": "no_mx:" + mx_reason,
                "mx_valid": "False", "flags": "|".join(flags)}
    return {"email_norm": norm, "decision": "keep",
            "drop_reason": "", "mx_valid": "" if mx_valid is None else "True",
            "flags": "|".join(flags)}
