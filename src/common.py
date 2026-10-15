"""Shared paths, config and state helpers."""
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
CONTENT = ROOT / "content"
STATE_DIR = ROOT / "state"
POSTS = ROOT / "posts"

STATE_FILE = STATE_DIR / "state.json"
HISTORY_FILE = STATE_DIR / "history.json"
TOPICS_FILE = CONTENT / "topics.txt"

DRY_RUN = os.environ.get("DRY_RUN") == "1"

# The save step in the workflows expects a posts/ folder; create it if the
# upload skipped the hidden posts/.gitkeep file.
try:
    POSTS.mkdir(exist_ok=True)
    (POSTS / ".gitkeep").touch(exist_ok=True)
except OSError:
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: Path, default):
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def load_config() -> dict:
    return load_json(ROOT / "config.json", {})


def load_state() -> dict:
    state = load_json(STATE_FILE, {})
    state.setdefault("last_update_id", 0)
    state.setdefault("last_theme", "dark")
    state.setdefault("series_counters", {})
    state.setdefault("drafts", [])
    state.setdefault("merged_seeds", [])
    # Older versions kept a single "pending" draft.
    old = state.pop("pending", None)
    if old and not any(d.get("id") == old.get("id") for d in state["drafts"]):
        state["drafts"].append(old)
    return state


def save_state(state: dict) -> None:
    save_json(STATE_FILE, state)


def load_history() -> list:
    return load_json(HISTORY_FILE, [])


def save_history(history: list) -> None:
    save_json(HISTORY_FILE, history)


def env(name: str, required: bool = True) -> str:
    value = os.environ.get(name, "").strip()
    if required and not value and not DRY_RUN:
        raise SystemExit(f"Missing required secret/environment variable: {name}")
    return value


# ---------- text clean-up applied to everything the writer produces ----------

def clean_text(text: str) -> str:
    """Enforce house style rules that should never depend on the AI behaving."""
    if not text:
        return text
    # No em dashes: turn " — " / "—" into a comma pause.
    text = re.sub(r"\s*[—―]\s*", ", ", text)
    # Plain hyphens and spaces instead of the special Unicode ones AI writers use.
    for ch in "\u2010\u2011\u2012\u2212":
        text = text.replace(ch, "-")
    for ch in "\u00a0\u2007\u2009\u200a\u202f":
        text = text.replace(ch, " ")
    for ch in "\u200b\u200c\u200d\u2060\ufeff\u00ad":
        text = text.replace(ch, "")
    # The bundled fonts have no rupee glyph.
    text = text.replace("₹", "Rs ")
    # Collapse doubled spaces and stray spaces before punctuation.
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r" +([,.;:!?])", r"\1", text)
    text = text.replace(",,", ",")
    return text.strip()


def topic_backlog() -> list:
    if not TOPICS_FILE.exists():
        return []
    lines = []
    for line in TOPICS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            lines.append(line)
    return lines


def remove_topic_from_backlog(topic: str) -> None:
    if not TOPICS_FILE.exists():
        return
    kept, removed = [], False
    for line in TOPICS_FILE.read_text(encoding="utf-8").splitlines():
        if not removed and line.strip() == topic.strip():
            removed = True
            continue
        kept.append(line)
    TOPICS_FILE.write_text("\n".join(kept).rstrip() + "\n", encoding="utf-8")


def add_topic_to_backlog(topic: str, front: bool = False) -> None:
    lines = TOPICS_FILE.read_text(encoding="utf-8").splitlines() if TOPICS_FILE.exists() else []
    if front:
        # Insert after any leading comment lines.
        i = 0
        while i < len(lines) and (lines[i].startswith("#") or not lines[i].strip()):
            i += 1
        lines.insert(i, topic.strip())
    else:
        lines.append(topic.strip())
    TOPICS_FILE.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def set_backlog(topics: list) -> None:
    """Rewrite the topic list in a new order, keeping the comment lines at the top."""
    lines = TOPICS_FILE.read_text(encoding="utf-8").splitlines() if TOPICS_FILE.exists() else []
    header = []
    for line in lines:
        if line.startswith("#") or not line.strip():
            header.append(line)
        else:
            break
    body = [t.strip() for t in topics if t.strip()]
    TOPICS_FILE.write_text("\n".join(header + body).rstrip() + "\n", encoding="utf-8")
