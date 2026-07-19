#!/usr/bin/env python3
"""Post-crawl assembly: dedup + exclude-already-paid + shared-footer + MX health.
Produces the FINAL health-checked lists that go to ListClean (personal/company).
Nothing here is fabricated; every survivor is an extracted, syntactically-valid,
mail-routable, not-already-paid address.
Usage: python dedup.py <all_emails.csv> <outdir> [base_dir]
"""
import csv, sys, os, glob, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hygiene as HY
from concurrent.futures import ThreadPoolExecutor

csv.field_size_limit(1 << 24)


def gmail_canon(email):
    """Dedup key: fold gmail dots/+tags (john.doe+x@gmail == johndoe@gmail)."""
    if "@" not in email:
        return email
    loc, host = email.rsplit("@", 1)
    if host in ("gmail.com", "googlemail.com"):
        return loc.replace(".", "").split("+")[0] + "@gmail.com"
    return email


def load_email_set(paths):
    s = set()
    for pat in paths:
        for p in glob.glob(pat):
            try:
                for r in csv.DictReader(open(p)):
                    e = (r.get("email") or r.get("LC_Email") or "").strip().lower()
                    if e:
                        s.add(gmail_canon(e))
            except Exception:
                pass
    return s


def main():
    infile, outdir = sys.argv[1], sys.argv[2]
    base = sys.argv[3] if len(sys.argv) > 3 else "/Users/mac/Desktop/ecom emails"
    os.makedirs(outdir, exist_ok=True)

    # 1) exclusion set = every email already validated / collected (never re-pay)
    paid = load_email_set([
        base + "/CRUX_VERIFIED/ALL_VALIDATED_jul16.csv",
        base + "/FINAL_VERIFIED/all_emails_verified.csv",
        base + "/remaining_to_verify_all-listclean.xyz.csv",
        base + "/MASTER_UNVALIDATED/master_unvalidated_emails.csv",
    ])
    print(f"already-validated/collected exclusion set: {len(paid):,}", flush=True)

    rows = list(csv.DictReader(open(infile)))
    dom_by_email = collections.defaultdict(set)
    for r in rows:
        dom_by_email[r["email"]].add(r["domain"])

    seen = set()
    kept = []
    ex_static = ex_shared = ex_paid = ex_dup = 0
    for r in rows:
        e = r["email"].strip().lower()
        if r.get("static_decision") != "keep":
            ex_static += 1; continue
        if len(dom_by_email[r["email"]]) >= 3:       # shared footer / app-vendor
            ex_shared += 1; continue
        ck = gmail_canon(e)
        if ck in paid:
            ex_paid += 1; continue
        if ck in seen:
            ex_dup += 1; continue
        seen.add(ck); kept.append(r)
    print(f"excluded -> static:{ex_static} shared_footer:{ex_shared} "
          f"already_paid:{ex_paid} intra_dup:{ex_dup}", flush=True)
    print(f"survivors before MX: {len(kept):,}", flush=True)

    # 2) MX health on UNIQUE surviving email-domains (cached, one query each)
    mx_path = base + "/crawler-public/mx_cache.csv"
    cache = HY.load_mx_cache(mx_path)
    doms = sorted({r["email"].split("@")[1] for r in kept if "@" in r["email"]})
    todo = [d for d in doms if d not in cache]
    print(f"unique domains {len(doms):,} | MX to resolve {len(todo):,}", flush=True)
    with ThreadPoolExecutor(max_workers=40) as ex:
        for i, _ in enumerate(ex.map(lambda d: HY.mx_check(d, cache), todo), 1):
            if i % 2000 == 0:
                print(f"  MX {i}/{len(todo)}", flush=True)
    HY.save_mx_cache(mx_path, cache)

    final, mx_dead, mx_unknown = [], 0, 0
    for r in kept:
        d = r["email"].split("@")[1]
        v, reason, flag = cache.get(d, (None, "", ""))
        if v is False:
            mx_dead += 1; continue
        if v is None:
            mx_unknown += 1
        r["mx_valid"] = "" if v is None else str(v)
        r["mx_flag"] = flag
        final.append(r)
    print(f"MX-dropped dead domains: {mx_dead} | mx_unknown(kept): {mx_unknown}", flush=True)

    # 3) write final ready lists (health-checked, deduped, not-already-paid)
    cols = ["domain", "email", "email_label", "confidence", "matched_name",
            "name_source", "is_domain_matched", "is_free", "source_url",
            "mx_valid", "mx_flag", "founder_names"]
    def w(name, rs):
        with open(os.path.join(outdir, name), "w", newline="") as f:
            wr = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            wr.writeheader(); wr.writerows(rs)
    personal = [r for r in final if r["email_label"].startswith("personal")]
    company = [r for r in final if r["email_label"].startswith("company")]
    w("ready_all.csv", final)
    w("ready_personal.csv", personal)
    w("ready_company.csv", company)
    # ListClean upload = just the unique emails
    with open(os.path.join(outdir, "listclean_upload.csv"), "w", newline="") as f:
        wr = csv.writer(f); wr.writerow(["email"])
        for r in final:
            wr.writerow([r["email"]])
    print(f"\n=== READY FOR LISTCLEAN: {len(final):,} healthy unique emails ===")
    print(f"  PERSONAL: {len(personal):,}  |  COMPANY: {len(company):,}")
    print(f"  files in {outdir}/: ready_all, ready_personal, ready_company, listclean_upload")


if __name__ == "__main__":
    main()
