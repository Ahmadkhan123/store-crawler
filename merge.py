#!/usr/bin/env python3
"""Merge all shard CSVs into final deliverables + a summary.

Outputs (in the given results dir):
  all_results.csv     every domain + status (full audit trail)
  emails_only.csv     just the stores where we found an email (outreach list)
  retry_needed.csv    domains that were throttled/dead -> feed a second wave
  summary.txt         headline stats
"""
import csv, sys, os, glob, collections

def main():
    shards_dir, out_dir = sys.argv[1], sys.argv[2]
    os.makedirs(out_dir, exist_ok=True)
    rows, seen = [], set()
    for fn in sorted(glob.glob(os.path.join(shards_dir, "*.csv"))):
        with open(fn) as f:
            for r in csv.DictReader(f):
                d = r.get("domain", "")
                if d and d not in seen:
                    seen.add(d); rows.append(r)
    fields = ["domain", "email", "source", "source_url", "email_type",
              "all_emails", "instagram", "facebook", "status"]

    with open(os.path.join(out_dir, "all_results.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader()
        w.writerows(rows)

    emails = [r for r in rows if r.get("email")]
    with open(os.path.join(out_dir, "emails_only.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader()
        w.writerows(emails)

    retry = [r for r in rows if r.get("status") in ("throttled", "dead", "error")]
    with open(os.path.join(out_dir, "retry_needed.csv"), "w", newline="") as f:
        w = csv.writer(f); w.writerow(["domain"])
        for r in retry:
            w.writerow([r["domain"]])

    st = collections.Counter(r.get("status", "") for r in rows)
    ty = collections.Counter(r.get("email_type", "") for r in emails)
    total = len(rows)
    lines = [
        f"Total domains processed : {total}",
        f"Emails found            : {len(emails)} ({len(emails)/max(total,1)*100:.1f}%)",
        f"  domain-matched        : {ty.get('domain-matched',0)}",
        f"  role/other            : {ty.get('role/other',0)}",
        f"  free-mail             : {ty.get('free-mail',0)}",
        f"No email (real)         : {st.get('no_email',0)}",
        f"Throttled (retry)       : {st.get('throttled',0)}",
        f"Dead                    : {st.get('dead',0)}",
        f"With Instagram link     : {sum(1 for r in rows if r.get('instagram'))}",
        f"With Facebook link      : {sum(1 for r in rows if r.get('facebook'))}",
    ]
    summ = "\n".join(lines)
    with open(os.path.join(out_dir, "summary.txt"), "w") as f:
        f.write(summ + "\n")
    print(summ)

if __name__ == "__main__":
    main()
