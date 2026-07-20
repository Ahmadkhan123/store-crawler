#!/usr/bin/env python3
"""Merge v2 shard CSVs (per-email rows) into combined outputs + summary.

Outputs (in results dir):
  all_emails.csv    every (domain,email) row with label + static hygiene
  summary.txt       label + hygiene counts
Shared-footer collapse, dedup vs registries, and MX run in the local dedup step.
"""
import csv, sys, os, glob, collections

COLS = ["domain", "email", "email_label", "confidence", "matched_name",
        "name_source", "is_domain_matched", "is_free", "source_url",
        "static_decision", "drop_reason", "founder_names", "status"]


def main():
    shards_dir, out_dir = sys.argv[1], sys.argv[2]
    os.makedirs(out_dir, exist_ok=True)
    seen, rows = set(), []
    for fn in sorted(glob.glob(os.path.join(shards_dir, "*.csv"))):
        if fn.endswith("_status.csv"):
            continue
        with open(fn) as f:
            for r in csv.DictReader(f):
                k = (r.get("domain", ""), r.get("email", ""))
                if k[1] and k not in seen:
                    seen.add(k); rows.append(r)
    with open(os.path.join(out_dir, "all_emails.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)

    emails = [r for r in rows if r.get("email")]
    cover = [r for r in rows if not r.get("email")]     # coverage rows (no email)
    lab = collections.Counter(r.get("email_label", "") for r in emails)
    cst = collections.Counter(r.get("status", "") for r in cover)
    pers = sum(v for k, v in lab.items() if k.startswith("personal"))
    lines = [
        f"Email rows              : {len(emails)}",
        f"PERSONAL (conf+likely)  : {pers}",
        *[f"  {k:22}: {v}" for k, v in lab.most_common() if k],
        f"Coverage rows (no email): {len(cover)}  -> " +
        " ".join(f"{k}:{v}" for k, v in cst.most_common()),
        f"RETRY set (throttled)   : {cst.get('throttled', 0)}",
    ]
    summ = "\n".join(lines)
    with open(os.path.join(out_dir, "summary.txt"), "w") as f:
        f.write(summ + "\n")
    print(summ)


if __name__ == "__main__":
    main()
