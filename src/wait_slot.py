"""Sleeps until the nearest posting slot or back-up time (within 45 minutes).
Used by the first job of '4. Post at the scheduled slots', which runs every
hour, so the posting times can be changed in the app without editing the
workflow. Tells the posting job whether to run (go=true) through
GITHUB_OUTPUT. Standard library only, no installs needed."""
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
ROOT = Path(__file__).resolve().parent.parent
cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
try:
    state = json.loads((ROOT / "state" / "state.json").read_text(encoding="utf-8"))
except (OSError, ValueError):
    state = {}


def plus_hour(t):
    hh, mm = (int(x) for x in t.split(":"))
    return f"{(hh + 1) % 24:02d}:{mm:02d}"


slots = cfg.get("slots", [])
chosen = state.get("slot_settings")  # posting times set in the app win over config.json
if isinstance(chosen, list):
    on = [{"time_ist": x["time"], "backup_ist": plus_hour(x["time"])} for x in chosen
          if isinstance(x, dict) and x.get("on") and re.fullmatch(r"\d\d:\d\d", str(x.get("time", "")))]
    slots = on or slots

now = datetime.now(IST)
times = []
for s in slots:
    for key in ("time_ist", "backup_ist"):
        if not s.get(key):
            continue
        hh, mm = (int(x) for x in s[key].split(":"))
        for d in (-1, 0, 1):
            day = now.date() + timedelta(days=d)
            times.append(datetime(day.year, day.month, day.day, hh, mm, tzinfo=IST))
upcoming = sorted(t for t in times if 0 < (t - now).total_seconds() <= 45 * 60)
recent = [t for t in times if 0 <= (now - t).total_seconds() <= 20 * 60]  # GitHub started this run late
manual = os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
go = bool(upcoming or recent or manual)
if upcoming:
    wait = (upcoming[0] - now).total_seconds()
    print(f"Waiting {int(wait)}s for {upcoming[0].isoformat()}", flush=True)
    time.sleep(wait)
elif recent:
    print(f"The {recent[-1].strftime('%H:%M')} IST slot just passed; posting now.")
else:
    print("No posting slot in the next 45 minutes" + ("; posting anyway (started by hand)." if manual else ", so nothing to do."))
out = os.environ.get("GITHUB_OUTPUT")
if out:
    with open(out, "a", encoding="utf-8") as f:
        f.write(f"go={'true' if go else 'false'}\n")
