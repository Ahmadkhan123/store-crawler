#!/usr/bin/env python3
"""Distributed v2 crawler (one shard per GitHub runner, each a distinct IP).

Per store: fetch pages -> extract emails (repair+syntax, extraction-only, no
fabrication) -> harvest founder names -> LABEL each email personal/company ->
static hygiene (disposable/platform/placeholder). One row per (domain,email),
each keeping its source_url. MX health runs later (once per unique domain,
post-dedup) so we never re-query the same mail host millions of times.

429s -> 'throttled' (retried on fresh IPs), never mislabeled 'no email'.
Usage: python crawler.py --shard I --of N [--limit K] --infile X --out Y
"""
import re, csv, os, time, random, argparse, warnings, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
import classify as C
import hygiene as HY
warnings.filterwarnings("ignore")
requests.packages.urllib3.disable_warnings()

UA_POOL = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]
WORKERS = int(os.environ.get("WORKERS", "10"))
PATHS = ["", "/pages/contact", "/contact", "/contact-us", "/pages/about", "/about",
         "/about-us", "/pages/our-story", "/policies/refund-policy"]
EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

OUT_COLS = ["domain", "email", "email_label", "confidence", "matched_name",
            "name_source", "is_domain_matched", "is_free", "source_url",
            "static_decision", "drop_reason", "founder_names", "status"]


def get(session, url, timeout=(8, 15)):
    return session.get(url, headers={"User-Agent": random.choice(UA_POOL)},
                       timeout=timeout, verify=False, allow_redirects=True)


def fetch(session, url):
    """(html, status): ok/notfound/throttled/dead. 429 retried then 'throttled'."""
    for attempt in range(3):
        try:
            r = get(session, url, timeout=(8, 15) if attempt == 0 else (12, 22))
            if r.status_code in (429, 503):
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
    pages, throttled = [], False
    home, st = fetch(session, "https://" + domain)
    if home is None and st != "throttled":
        home, st = fetch(session, "http://" + domain)
    if st == "throttled":
        throttled = True
    if home is None:
        return domain, [], ("throttled" if throttled else "dead")
    pages.append(("https://" + domain, home))
    root = domain.replace("www.", "").split(".")[0].lower()
    for p in PATHS[1:]:
        html, st = fetch(session, "https://" + domain + p)
        if st == "throttled":
            throttled = True
            continue
        if html:
            pages.append(("https://" + domain + p, html))
        if sum(len(h) for _, h in pages) > 1200000:
            break

    # extract emails with source_url (first page each appears on)
    email_src = {}
    for url, html in pages:
        for e in re.findall(r'mailto:([^"\'>?]+)', html) + EMAIL_RE.findall(html):
            n = HY.normalize_repair(e)
            if HY.syntax_ok(n) and "@" in n and not any(v in n.split("@")[1] for v in C.APP_VENDOR):
                email_src.setdefault(n, url)
    all_html = "\n".join(h for _, h in pages)
    names = C.harvest_names(all_html, C.brand_tokens(all_html, domain))
    text_fold = C.defold(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", all_html)))[:300000]
    fnames = ";".join(n[0] for n in names[:3])

    rows = []
    for e, src in email_src.items():
        lab = C.classify(e, domain, text_fold, names)
        host = e.split("@")[1]
        is_dm = bool(root) and root in host
        is_free = any(f in host for f in C.FREE_HOSTS)
        dec, reason, _ = HY.static_verdict(e)     # no-network hygiene (MX later)
        rows.append({"domain": domain, "email": e, "email_label": lab["label"],
                     "confidence": lab["confidence"], "matched_name": lab["matched_name"],
                     "name_source": lab["name_source"], "is_domain_matched": int(is_dm),
                     "is_free": int(is_free), "source_url": src,
                     "static_decision": dec, "drop_reason": reason,
                     "founder_names": fnames, "status": "ok"})
    status = "ok" if rows else ("throttled" if throttled else "no_email")
    return domain, rows, status


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shard", type=int, required=True)
    ap.add_argument("--of", type=int, required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--infile", default="domains.csv")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    with open(a.infile) as f:
        alld = [l.strip() for l in f if l.strip() and l.strip() != "domain"]
    mine = alld[a.shard::a.of]
    if a.limit:
        mine = mine[:a.limit]
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    print(f"shard {a.shard}/{a.of}: {len(mine)} domains | workers {WORKERS}", flush=True)

    t0, n, nem, npers, thr, dead = time.time(), 0, 0, 0, 0, 0
    status_rows = []
    with open(a.out, "w", newline="") as out:
        w = csv.DictWriter(out, fieldnames=OUT_COLS)
        w.writeheader()
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            futs = {ex.submit(scrape, d): d for d in mine}
            for fut in as_completed(futs):
                d = futs[fut]
                try:
                    dom, rows, st = fut.result()
                except Exception:
                    dom, rows, st = d, [], "error"
                for r in rows:
                    w.writerow(r)
                    if r["email_label"].startswith("personal"):
                        npers += 1
                nem += len(rows)
                if st == "throttled":
                    thr += 1
                if st == "dead":
                    dead += 1
                status_rows.append((dom, st, len(rows)))
                n += 1
                if n % 200 == 0:
                    out.flush()
                    rate = n / (time.time() - t0)
                    print(f"  {n}/{len(mine)} | emails {nem} personal {npers} | "
                          f"throttled {thr} dead {dead} | {rate:.1f}/s", flush=True)
    with open(a.out.replace(".csv", "_status.csv"), "w", newline="") as sf:
        sw = csv.writer(sf); sw.writerow(["domain", "status", "n_emails"])
        sw.writerows(status_rows)
    print(f"SHARD {a.shard} DONE: {n} domains | {nem} emails ({npers} personal) | "
          f"throttled {thr} dead {dead} | {(time.time()-t0)/60:.1f} min", flush=True)


if __name__ == "__main__":
    main()
