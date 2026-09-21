from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATA = BASE / "site" / "data"
NEWS_FILE = DATA / "news.json"
FOCUS_FILE = DATA / "weekly_focus.json"

FOCUS_DAYS = max(1, int(os.getenv("WEEKLY_FOCUS_DAYS", "7")))
FOCUS_COUNT = min(10, max(5, int(os.getenv("WEEKLY_FOCUS_COUNT", "8"))))
MAX_PER_SOURCE = max(1, int(os.getenv("WEEKLY_FOCUS_MAX_PER_SOURCE", "3")))

TAG_ORDER = ["竞对资讯", "招标采购", "市场机制", "电网建设", "宏观政策", "审批政策"]
TAG_WEIGHTS = {
    "市场机制": 20,
    "宏观政策": 18,
    "招标采购": 18,
    "审批政策": 15,
    "电网建设": 14,
    "竞对资讯": 12,
}
CAPACITY_RE = re.compile(r"(?<![\w.])(\d{1,4}(?:[.,]\d+)?)\s*[-–—]?\s*(GW|MW)\b", re.I)


def clean(v) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()


def load_news() -> list[dict]:
    try:
        data = json.loads(NEWS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def parse_day(raw: str):
    try:
        return datetime.strptime(clean(raw), "%Y-%m-%d").date()
    except Exception:
        return None


def max_capacity_mw(text: str) -> float:
    vals = []
    for num, unit in CAPACITY_RE.findall(text):
        try:
            v = float(num.replace(",", "."))
        except ValueError:
            continue
        if unit.upper() == "GW":
            v *= 1000
        if 0 < v < 100000:
            vals.append(v)
    return max(vals, default=0.0)


def normalized_title(title: str) -> str:
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", clean(title).lower())
    stop = {"wind","energy","project","farm","offshore","onshore","the","a","an","of","for","to","in"}
    return " ".join(x for x in text.split() if x not in stop)


def too_similar(a: dict, b: dict) -> bool:
    ta, tb = normalized_title(a.get("title","")), normalized_title(b.get("title",""))
    if not ta or not tb:
        return False
    if SequenceMatcher(None, ta, tb).ratio() >= 0.78:
        return True
    sa, sb = set(ta.split()), set(tb.split())
    return bool(sa and sb) and len(sa & sb) / len(sa | sb) >= 0.68


def score_item(item: dict, today) -> float:
    d = parse_day(item.get("published_at",""))
    age = max(0, (today - d).days if d else FOCUS_DAYS)
    tags = item.get("tags") or []
    score = max(0, 12 - age * 1.5)
    score += max((TAG_WEIGHTS.get(t, 0) for t in tags), default=0)
    if len(tags) >= 2:
        score += 5
    if len(tags) >= 3:
        score += 3

    text = " ".join([
        clean(item.get("title")),
        clean(item.get("translated_title")),
        clean(item.get("excerpt"))[:900],
    ])
    cap = max_capacity_mw(text)
    if cap >= 2000:
        score += 16
    elif cap >= 1000:
        score += 13
    elif cap >= 500:
        score += 9
    elif cap >= 200:
        score += 6
    elif cap >= 100:
        score += 3

    score += min(float(item.get("strategic_score", 0) or 0), 18) * 0.5
    if item.get("competitors"):
        score += 3
    return round(score, 2)


def format_capacity(mw: float) -> str:
    if mw >= 1000:
        return f"{mw/1000:.1f}".rstrip("0").rstrip(".") + " GW"
    return f"{int(round(mw))} MW"


def signals(item: dict) -> list[str]:
    out = []
    tags = item.get("tags") or []
    text = " ".join([
        clean(item.get("title")),
        clean(item.get("translated_title")),
        clean(item.get("excerpt"))[:800],
    ])
    cap = max_capacity_mw(text)
    if cap >= 100:
        out.append(format_capacity(cap))
    out.extend(tags[:2])
    for company in item.get("competitors", [])[:1]:
        out.append(company)
    reason = clean(item.get("monitor_reason"))
    if reason and len(out) < 3:
        out.append(reason.split(" · ")[0])
    dedup = []
    for x in out:
        if x and x not in dedup:
            dedup.append(x)
    return dedup[:3]


def main() -> None:
    news = load_news()
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=FOCUS_DAYS - 1)

    candidates = []
    for item in news:
        d = parse_day(item.get("published_at",""))
        if not d or d < start or d > today:
            continue
        if not item.get("tags"):
            continue
        x = {
            "country": clean(item.get("country")),
            "source_name": clean(item.get("source_name")),
            "title": clean(item.get("title")),
            "translated_title": clean(item.get("translated_title")),
            "published_at": clean(item.get("published_at")),
            "url": clean(item.get("url")),
            "tags": [t for t in TAG_ORDER if t in (item.get("tags") or [])],
            "competitors": item.get("competitors", []),
            "monitor_reason": clean(item.get("monitor_reason")),
        }
        x["_score"] = score_item(item, today)
        x["signals"] = signals(item)
        candidates.append(x)

    candidates.sort(key=lambda x: (x["_score"], x["published_at"]), reverse=True)

    selected = []
    source_counts = {}

    def can_add(x):
        if source_counts.get(x["source_name"], 0) >= MAX_PER_SOURCE:
            return False
        return not any(too_similar(x, old) for old in selected)

    def add(x):
        selected.append(x)
        source_counts[x["source_name"]] = source_counts.get(x["source_name"], 0) + 1

    # 优先保证关键类别有代表性，但不强行凑数。
    for tag in ["市场机制", "招标采购", "宏观政策", "审批政策", "电网建设", "竞对资讯"]:
        if len(selected) >= FOCUS_COUNT:
            break
        best = next((x for x in candidates if x not in selected and tag in x["tags"] and can_add(x)), None)
        if best:
            add(best)

    for x in candidates:
        if len(selected) >= FOCUS_COUNT:
            break
        if x in selected:
            continue
        if can_add(x):
            add(x)

    # 如果当周确实很少，允许少于5条；宁缺毋滥。
    for i, x in enumerate(selected, start=1):
        x["rank"] = i
        x["importance_reason"] = " · ".join(x.get("signals", []))
        x.pop("_score", None)

    counts = {tag: sum(1 for x in selected if tag in x.get("tags", [])) for tag in TAG_ORDER}
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "period": {"days": FOCUS_DAYS, "start": start.isoformat(), "end": today.isoformat()},
        "selection_note": "仅从六类战略监测新闻中自动精选：政策与机制优先，其次为重大招标、竞对项目、电网与审批节点。",
        "count": len(selected),
        "tag_order": TAG_ORDER,
        "tag_counts": counts,
        "items": selected,
    }
    FOCUS_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"weekly focus generated: {len(selected)} items tags={counts}")


if __name__ == "__main__":
    main()
