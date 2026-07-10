#!/usr/bin/env python3
"""Read run.json and emit shard config to $GITHUB_OUTPUT for the matrix."""
import json, os
c = json.load(open("run.json"))
s = int(c.get("shards", 20))
with open(os.environ["GITHUB_OUTPUT"], "a") as f:
    f.write("shards=%d\n" % s)
    f.write("limit=%d\n" % int(c.get("limit", 0)))
    f.write("workers=%d\n" % int(c.get("workers", 10)))
    f.write("matrix=%s\n" % json.dumps(list(range(s))))
print("shards=%d limit=%s workers=%s" % (s, c.get("limit"), c.get("workers")))
