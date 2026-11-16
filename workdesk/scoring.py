"""Deterministic urgency / importance scoring. Every score carries a short reason."""
from .models import parse_iso, local_date
from datetime import date

PRIORITY = {"highest": 90, "high": 75, "medium": 55, "low": 35, "lowest": 20}
NUDGE_HOURS = 120
THRESHOLD = 60


def hours_since(item, now):
    dt = parse_iso(item.get("updated_at")) or parse_iso(item.get("created_at"))
    return 0.0 if dt is None else max(0.0, (now - dt).total_seconds() / 3600)


def is_nudge(item, now):
    return item.get("kind") == "waiting" and hours_since(item, now) >= NUDGE_HOURS


def _hot(item, cfg):
    hot = {h.lower() for h in cfg["general"]["hot_labels"]}
    return sorted(l for l in item.get("labels", []) if l.lower() in hot)


def compute_urgency(item, now, cfg):
    cands = []
    kind, meta = item.get("kind"), item.get("meta") or {}
    if item.get("due"):
        days = (date.fromisoformat(item["due"]) - local_date(now, cfg["general"]["timezone"])).days
        if days < 0:
            cands.append((100, f"overdue by {-days}d"))
        elif days == 0:
            cands.append((90, "due today"))
        elif days == 1:
            cands.append((80, "due tomorrow"))
        elif days <= 3:
            cands.append((65, f"due in {days}d"))
        elif days <= 7:
            cands.append((45, f"due in {days}d"))
        else:
            cands.append((20, f"due in {days}d"))
    hot = _hot(item, cfg)
    if hot:
        cands.append((95, "hot label " + hot[0]))
    h = hours_since(item, now)
    if kind in ("respond", "review"):
        for lim, sc in ((72, 90), (48, 80), (24, 70), (8, 55), (2, 40)):
            if h >= lim:
                cands.append((sc, f"no reply for {int(h)}h"))
                break
        else:
            cands.append((30, "recent"))
    elif kind == "do" and not item.get("due"):
        if meta.get("in_sprint") and meta.get("in_progress"):
            cands.append((45, "in progress in active sprint"))
        elif meta.get("in_sprint"):
            cands.append((35, "in active sprint"))
        else:
            cands.append((15, "no due date"))
    elif kind == "waiting":
        if h >= NUDGE_HOURS:
            cands.append((50, f"nudge: quiet for {int(h // 24)}d"))
        else:
            cands.append((10, "waiting"))
    if not cands:  # do with due handled above; safety net
        cands.append((15, "no signal"))
    best = max(cands, key=lambda c: c[0])
    return best


def compute_importance(item, cfg):
    kind, source, meta = item.get("kind"), item.get("source"), item.get("meta") or {}
    parts = []
    if source == "jira":
        pr = (meta.get("priority") or "").lower()
        score = PRIORITY.get(pr, 50)
        parts.append(f"priority {meta.get('priority') or 'unset'}")
        if meta.get("in_sprint"):
            score += 10
            parts.append("sprint +10")
        if kind == "waiting" and score > 45:
            score = 45
            parts.append("waiting cap 45")
    elif source == "manual":
        score = 50
        parts.append("manual")
    elif kind == "review":
        score = 60
        parts.append("review")
    elif kind == "respond":
        if source == "slack" and meta.get("is_dm"):
            score, label = 60, "direct message"
        elif source == "slack":
            score, label = 50, "slack mention"
        else:
            score, label = 55, "mention"
        score = score
        parts.append(label)
    elif kind == "waiting":
        score = 35
        parts.append("waiting")
    else:
        score = 50
        parts.append("assigned")
    if _hot(item, cfg):
        score += 25
        parts.append("hot +25")
    vips = {v.lower() for v in cfg["general"]["vip_requesters"]}
    if item.get("requester") and item["requester"].lower() in vips:
        score += 20
        parts.append("vip +20")
    return max(0, min(100, score)), ", ".join(parts)


def quadrant(urgency, importance):
    if importance >= THRESHOLD and urgency >= THRESHOLD:
        return "do_now"
    if importance >= THRESHOLD:
        return "schedule"
    if urgency >= THRESHOLD:
        return "quick"
    return "later"


def score_item(item, now, cfg):
    u, ur = compute_urgency(item, now, cfg)
    i, ir = compute_importance(item, cfg)
    if item.get("urgency_override") is not None:
        u, ur = item["urgency_override"], "override"
    if item.get("importance_override") is not None:
        i, ir = item["importance_override"], "override"
    out = dict(item)
    out.update(urgency=u, urgency_reason=ur, importance=i, importance_reason=ir,
               rank=round(0.6 * u + 0.4 * i, 1), quadrant=quadrant(u, i), nudge=is_nudge(item, now))
    return out


def rank_items(items, now, cfg):
    scored = [score_item(i, now, cfg) for i in items]
    scored.sort(key=lambda s: (-s["rank"], s["id"]))
    return scored
