"""Shared data types and small helpers (time, levels)."""
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, date
from zoneinfo import ZoneInfo

SOURCES = ("jira", "github", "slack", "manual")
KINDS = ("do", "respond", "review", "waiting")
LEVELS = {"high": 85, "medium": 55, "low": 25}


def utcnow():
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s):
    """Parse an ISO 8601 string (any offset, Z allowed) to an aware UTC datetime; None if empty."""
    if s is None or s == "":
        return None
    s = str(s).strip()
    if s.endswith("Z") or s.endswith("z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0)


def norm_ts(s):
    """Normalise any source timestamp to our stored form, or None."""
    dt = parse_iso(s)
    return iso(dt) if dt else None


def from_epoch(sec):
    return datetime.fromtimestamp(int(float(sec)), timezone.utc)


def local_date(now, tz):
    return now.astimezone(ZoneInfo(tz)).date()


def parse_level(text):
    """'auto' -> None, high/medium/low -> 85/55/25, or an integer 0..100."""
    if text is None:
        return None
    t = str(text).strip().lower()
    if t in ("", "auto", "none", "null"):
        return None
    if t in LEVELS:
        return LEVELS[t]
    v = int(t)
    if not 0 <= v <= 100:
        raise ValueError("value must be 0..100")
    return v


def parse_day(text):
    return date.fromisoformat(text)


@dataclass
class Item:
    id: str
    source: str
    kind: str
    title: str
    url: str = ""
    requester: str = ""
    status: str = ""
    due: str = None
    created_at: str = None
    updated_at: str = None
    labels: list = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)
