"""Sleeps until the nearest posting slot or back-up time (within 45 minutes).
Used by the first job of '4. Post at the scheduled slots', so the waiting
doesn't block the app's actions. Standard library only, no installs needed."""
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
cfg = json.loads((Path(__file__).resolve().parent.parent / "config.json").read_text(encoding="utf-8"))
now = datetime.now(IST)
times = []
for s in cfg.get("slots", []):
    for key in ("time_ist", "backup_ist"):
        if not s.get(key):
            continue
        hh, mm = (int(x) for x in s[key].split(":"))
        for d in (-1, 0, 1):
            day = now.date() + timedelta(days=d)
            times.append(datetime(day.year, day.month, day.day, hh, mm, tzinfo=IST))
upcoming = sorted(t for t in times if 0 < (t - now).total_seconds() <= 45 * 60)
if upcoming:
    wait = (upcoming[0] - now).total_seconds()
    print(f"Waiting {int(wait)}s for {upcoming[0].isoformat()}", flush=True)
    time.sleep(wait)
else:
    print("No slot in the next 45 minutes; posting whatever is due now.")
