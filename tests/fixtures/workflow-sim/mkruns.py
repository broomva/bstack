#!/usr/bin/env python3
"""Write a `gh run list --json conclusion,createdAt,status,name,url` fixture that breaches.

    mkruns.py <out.json>

Thirty days of 20 runs each, relative to the clock: the templates' series step drops the
current UTC day by the clock, so fixed dates would age out of the band's window. The
baseline fails about 1 run in 10; yesterday fails 12 in 20, far past 3 sigma.
"""
import datetime as dt
import json
import sys

now = dt.datetime.now(dt.timezone.utc)
runs = []
for back in range(30):
    day = (now - dt.timedelta(days=back)).replace(hour=1, minute=0, second=0, microsecond=0)
    for i in range(20):
        t = day + dt.timedelta(minutes=30 * i)
        if t > now:
            continue
        fail = i < 12 if back == 1 else (i % 10 == 0 or (back % 3 == 0 and i == 5))
        runs.append({"conclusion": "failure" if fail else "success", "status": "completed",
                     "createdAt": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "name": "CI",
                     "url": f"https://github.invalid/owner/repo/actions/runs/{len(runs) + 1}"})
json.dump(runs, open(sys.argv[1], "w"))
