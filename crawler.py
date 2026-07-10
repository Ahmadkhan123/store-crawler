#!/usr/bin/env python3
"""Distributed Shopify contact-email crawler (one shard per GitHub runner).

Each runner has its own IP, so Shopify's per-IP throttle never trips.
Fresh, live data. Extraction-only -> no fabrication; every email keeps the
exact source URL it was scraped from. 429s are backed-off and, if persistent,
recorded as `throttled` (NOT dead) so a later wave can retry them.

Usage: python crawler.py --shard I --of N [--limit K] --out out/shard-I.csv
"""
import re, csv, os, time, random, argparse, warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
warnings.filterwarnings("ignore")
requests.packages.urllib3.disable_warnings()

UA_POOL = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]
WORKERS = int(os.environ.get("WORKERS", "10"))   # polite per-IP concurrency

PATHS = ["", "/pages/contact", "/pages/contact-us", "/contact", "/contact-us",
         "/pages/about", "/pages/about-us", "/about", "/policies/refund-policy"]

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
SOCIAL_RE = {
    "instagram": re.compile(r'https?://(?:www\.)?instagram\.com/([A-Za-z0-9_.]+)'),
    "facebook":  re.compile(r'https?://(?:www\.)?facebook\.com/([A-Za-z0-9_.\-/]+)'),
}
BAD = ("sentry", "wixpress", "example", "godaddy", "shopify.com", "jsdelivr",
       "w3.org", "schema.org", "googleapis", "cloudflare", "@2x", "@3x",
       ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js",
       "klaviyo", "mailchimp", "cdn.", "gstatic", "your@", "youremail",
       "sample@", "@email.com", "@example", "test@", "user@", "name@",
       "yourdomain", "yourstore", "fontawesome", "recaptcha", "wix.com",
       "sentry.io", "email.com", "domain.com")
ROLE = ("info", "support", "hello", "contact", "sales", "help", "care",
        "service", "team", "admin", "orders", "customerservice", "hi", "shop")
FREE = ("gmail", "yahoo", "hotmail", "outlook", "icloud", "aol", "gmx",
        "proton", "live.com", "msn")

def clean(cands):
    seen, out = set(), []
    for e in cands:
        el = e.lower().strip(".")
        if el.count("@") != 1:
            continue
        host = el.split("@")[1]
        if "." not in host or host.startswith(".") or host.endswith("."):
            continue
        if any(b in el for b in BAD) or len(el) > 60 or el in seen:
            continue
        seen.add(el); out.append(el)
    return out

def pick_best(emails, domain):
    root = domain.replace("www.", "").split(".")[0].lower()
    for e in emails:
        if root and root in e.split("@")[1]:
            return e
    for e in emails:
        if e.split("@")[0] in ROLE:
            return e
    return emails[0] if emails else ""

def etype(domain, em):
    if not em:
        return ""
    host = em.split("@")[-1]
    if domain.split(".")[0] in host:
        return "domain-matched"
    if any(f in host for f in FREE):
        return "free-mail"
    return "role/other"

def get(session, url, timeout=(8, 15)):
    return session.get(url, headers={"User-Agent": random.choice(UA_POOL)},
                       timeout=timeout, verify=False, allow_redirects=True)

def fetch(session, url):
    """Return (html, status). status in ok/notfound/throttled/dead.
    429 is retried with backoff, then reported as 'throttled' (never dead)."""
    for attempt in range(3):
        try:
            r = get(session, url, timeout=(8, 15) if attempt == 0 else (12, 22))
            if r.status_code == 429 or r.status_code == 503:
                time.sleep(2 * (attempt + 1) + random.random())
                if attempt == 2:
                    return None, "throttled"
                continue
            if r.status_code >= 400:
                return None, "notfound"
            return r.text, "ok"
        except requests.RequestException:
            if attempt < 2:
                time.sleep(0.5 + random.random())
    return None, "dead"

def scrape(domain):
    session = requests.Session()
    found, socials, source = [], {}, ""
    root = domain.replace("www.", "").split(".")[0].lower()
    throttled_any = False
    # homepage first (email + socials), trying https then http
    home, st = fetch(session, "https://" + domain)
    if home is None and st != "throttled":
        home, st = fetch(session, "http://" + domain)
    if st == "throttled":
        throttled_any = True
    if home is None:
        return (domain, "", [], "", {},
                "throttled" if throttled_any else "dead")
    for m in clean(re.findall(r'mailto:([^"\'>?]+)', home) +
                   EMAIL_RE.findall(home)):
        if m not in found:
            found.append(m)
    if found:
        source = "https://" + domain
    for net, rx in SOCIAL_RE.items():
        for handle in rx.findall(home):
            h = handle.strip("/").split("/")[0].lower()
            if h and h not in ("sharer", "tr", "plugins", "dialog",
                               "profile.php", "pages", "home.php", "sharer.php"):
                socials.setdefault(net, "https://%s.com/%s" %
                                   (net, handle.strip("/")))
                break
    if pick_best(found, domain).split("@")[-1].startswith(root):
        return domain, pick_best(found, domain), found, source, socials, "ok"
    # try contact/about/policy pages
    for path in PATHS[1:]:
        html, st = fetch(session, "https://" + domain + path)
        if st == "throttled":
            throttled_any = True
            continue
        if html is None:
            continue
        new = clean(re.findall(r'mailto:([^"\'>?]+)', html) +
                    EMAIL_RE.findall(html))
        if new:
            for m in new:
                if m not in found:
                    found.append(m)
            if not source:
                source = "https://" + domain + path
            if pick_best(found, domain).split("@")[-1].startswith(root):
                source = "https://" + domain + path
                break
    best = pick_best(found, domain)
    status = "ok" if best else ("throttled" if throttled_any else "no_email")
    return domain, best, found, source, socials, status

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shard", type=int, required=True)
    ap.add_argument("--of", type=int, required=True)
    ap.add_argument("--limit", type=int, default=0,
                    help="max domains this shard processes (0=all); for pilots")
    ap.add_argument("--infile", default="domains.csv")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    with open(a.infile) as f:
        alld = [l.strip() for l in f if l.strip() and l.strip() != "domain"]
    mine = alld[a.shard::a.of]                 # strided => balanced shards
    if a.limit:
        mine = mine[:a.limit]
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    print(f"shard {a.shard}/{a.of}: {len(mine)} domains | workers {WORKERS}",
          flush=True)

    t0, n, hits, thr, dead = time.time(), 0, 0, 0, 0
    with open(a.out, "w", newline="") as out:
        w = csv.writer(out)
        w.writerow(["domain", "email", "source", "source_url", "email_type",
                    "all_emails", "instagram", "facebook", "status"])
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            futs = {ex.submit(scrape, d): d for d in mine}
            for fut in as_completed(futs):
                d = futs[fut]
                try:
                    dom, best, allm, src, soc, stt = fut.result()
                except Exception:
                    dom, best, allm, src, soc, stt = d, "", [], "", {}, "error"
                if best:
                    hits += 1
                if stt == "throttled":
                    thr += 1
                if stt == "dead":
                    dead += 1
                w.writerow([dom, best, "website" if best else "", src,
                            etype(dom, best), ";".join(allm),
                            soc.get("instagram", ""), soc.get("facebook", ""),
                            stt])
                n += 1
                if n % 100 == 0:
                    out.flush()
                    rate = n / (time.time() - t0)
                    print(f"  {n}/{len(mine)} | emails {hits} | throttled "
                          f"{thr} | dead {dead} | {rate:.1f}/s", flush=True)
    print(f"SHARD {a.shard} DONE: {n} done | emails {hits} "
          f"({hits/max(n,1)*100:.0f}%) | throttled {thr} | dead {dead} | "
          f"{(time.time()-t0)/60:.1f} min", flush=True)

if __name__ == "__main__":
    main()
