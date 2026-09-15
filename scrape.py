from __future__ import annotations

import csv
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, quote_plus, urlencode, urljoin, urlparse, urlunparse

import dateparser
import feedparser
import requests
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE = Path(__file__).resolve().parent
DATA = BASE / "site" / "data"
DATA.mkdir(parents=True, exist_ok=True)
SOURCES = json.loads((BASE / "sources.json").read_text(encoding="utf-8"))
SOURCE_BY_NAME = {s["name"]: s for s in SOURCES}

NEWS_FILE = DATA / "news.json"
STATUS_FILE = DATA / "source_status.json"
META_FILE = DATA / "meta.json"
CSV_FILE = DATA / "wind_news.csv"

MAX_AGE_DAYS = int(os.getenv("MAX_AGE_DAYS", "120"))
MAX_KEEP_DAYS = int(os.getenv("MAX_KEEP_DAYS", "730"))
MAX_KEEP_ARTICLES = int(os.getenv("MAX_KEEP_ARTICLES", "1500"))
MAX_ARTICLES_PER_SOURCE = int(os.getenv("MAX_ARTICLES_PER_SOURCE", "24"))
MAX_CANDIDATES = int(os.getenv("MAX_CANDIDATES_PER_ENTRY", "36"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "24"))
MIN_HOST_INTERVAL = float(os.getenv("MIN_HOST_INTERVAL_SECONDS", "0.8"))
ENABLE_BROWSER = os.getenv("ENABLE_BROWSER_FALLBACK", "1") == "1"
BROWSER_TIMEOUT_MS = int(os.getenv("BROWSER_TIMEOUT_MS", "35000"))
MAX_EXCERPT_CHARS = int(os.getenv("MAX_EXCERPT_CHARS", "700"))
ENABLE_TRANSLATION = os.getenv("ENABLE_TRANSLATION", "1") == "1"
MAX_TRANSLATIONS = int(os.getenv("MAX_TRANSLATIONS_PER_RUN", "180"))
TRANSLATE_TIMEOUT = int(os.getenv("TRANSLATE_TIMEOUT", "15"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("wind-monitor-v4.2")

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0 Safari/537.36 WindPolicyMonitor/4.2"
)
HEADERS = {
    "User-Agent": UA,
    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

SESSION = requests.Session()
SESSION.mount(
    "https://",
    HTTPAdapter(
        max_retries=Retry(
            total=2,
            connect=2,
            read=2,
            backoff_factor=0.7,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=frozenset(["GET"]),
        )
    ),
)

WIND_TERMS = [
    "wind", "wind power", "wind energy", "wind farm", "windfarm", "offshore", "onshore",
    "turbine", "turbines", "repowering", "eólica", "eolica", "eólico", "eolico",
    "energia eólica", "energia eolica", "éolien", "éolienne", "windkraft", "windenergie",
    "rüzgar", "ruzgar", "ветр", "ветро", "жел энергиясы", "điện gió", "dien gio",
    "gió ngoài khơi", "طاقة الرياح", "رياح", "shamol",
]
BLOCKED = [
    "/category/", "/tag/", "/tags/", "/author/", "/search", "/privacy", "/cookie",
    "/about", "/contact", "/login", "/register", "/feed/", "/page/",
]
SKIP_EXT = (
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".pdf", ".zip", ".doc",
    ".docx", ".xls", ".xlsx", ".mp4", ".mp3", ".ico",
)
BAD_TITLES = {
    "qazaqgreen", "renewables now", "recharge", "windeurope", "windinsider", "anev",
    "news", "latest news", "home", "media & news", "newsletter", "wind power",
}
LAST_HOST: dict[str, float] = {}
TRANSLATION_CACHE: dict[str, str] = {}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean(v: str | None) -> str:
    return re.sub(r"\s+", " ", v or "").strip()


def is_wind(text: str) -> bool:
    low = clean(text).lower()
    return any(t in low for t in WIND_TERMS)


def norm(url: str) -> str:
    try:
        p = urlparse(url)
        if p.scheme not in ("http", "https"):
            return ""
        q = [
            (k, v)
            for k, v in parse_qsl(p.query, keep_blank_values=True)
            if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"}
        ]
        path = re.sub(r"/{2,}", "/", p.path or "/")
        return urlunparse((p.scheme, p.netloc.lower(), path, "", urlencode(q), ""))
    except Exception:
        return ""


def same_host(a: str, b: str) -> bool:
    return urlparse(a).netloc.lower().removeprefix("www.") == urlparse(b).netloc.lower().removeprefix("www.")


def expand_template(tpl: str, query: str | None = None) -> str:
    """Expand only the placeholders we own. Unknown braces never raise KeyError."""
    now = datetime.now()
    months = [
        "january", "february", "march", "april", "may", "june",
        "july", "august", "september", "october", "november", "december",
    ]
    values = {
        "year": str(now.year),
        "month": str(now.month),
        "day": str(now.day),
        "month_name_lower": months[now.month - 1],
    }
    out = tpl
    for key, value in values.items():
        out = out.replace("{" + key + "}", value)
    if query is not None:
        out = out.replace("{query}", quote_plus(query))
    return out


def fix_source_url(url: str, source: dict) -> str:
    url = norm(url)
    if not url:
        return ""
    if source.get("id") == "energetica_india":
        p = urlparse(url)
        path = p.path
        path = path.replace("/news/renewable-energy/wind-power/news/", "/news/")
        path = path.replace("/news/renewable-energy/news/", "/news/")
        path = re.sub(r"^/news/renewable-energy/(?:wind-power/)?news/", "/news/", path)
        url = urlunparse((p.scheme, p.netloc, path, "", p.query, ""))
    return norm(url)


def entry_urls(source: dict) -> set[str]:
    return {fix_source_url(expand_template(u), source) for u in source.get("entry_urls", [])}


def article_url(url: str, source: dict) -> bool:
    url = fix_source_url(url, source)
    if not url or url in entry_urls(source):
        return False
    p = urlparse(url)
    low = (p.path or "/").lower()
    if low in ("", "/") or any(x in low for x in BLOCKED) or url.lower().endswith(SKIP_EXT):
        return False
    q = dict(parse_qsl(p.query))
    if any(k.lower() in {"id", "article", "newsid", "story", "post"} and str(v).strip() for k, v in q.items()):
        return True
    if source.get("allow_single_segment_articles") and low.endswith((".html", ".htm")):
        return True
    seg = [x for x in p.path.split("/") if x]
    if re.search(r"/20\d{2}/", p.path):
        return True
    if any(h.lower() in low for h in source.get("article_path_hints", [])) and len(seg) >= 1:
        return True
    return len(seg) >= 3


def parse_date(raw: str | None) -> str | None:
    raw = clean(raw)
    if not raw:
        return None
    try:
        dt = dateparser.parse(
            raw,
            settings={
                "PREFER_DATES_FROM": "past",
                "RETURN_AS_TIMEZONE_AWARE": False,
                "DATE_ORDER": "DMY",
            },
        )
        return dt.date().isoformat() if dt else None
    except Exception:
        return None


def date_url(url: str) -> str | None:
    pats = (
        r"/(20\d{2})/(\d{1,2})/(\d{1,2})(?:/|$)",
        r"/(20\d{2})-(\d{1,2})-(\d{1,2})(?:/|$)",
        r"/sitemaps/(20\d{2})/([a-z]+)/([0-3]?\d)/?",
    )
    for pat in pats[:2]:
        m = re.search(pat, url, re.I)
        if m:
            try:
                return datetime(*map(int, m.groups())).date().isoformat()
            except Exception:
                pass
    m = re.search(pats[2], url, re.I)
    if m:
        month_names = {
            name.lower(): i
            for i, name in enumerate(
                ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"],
                start=1,
            )
        }
        try:
            return datetime(int(m.group(1)), month_names[m.group(2).lower()], int(m.group(3))).date().isoformat()
        except Exception:
            pass
    return None


def date_text(text: str) -> str | None:
    text = clean(text)[:3600]
    pats = [
        r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},?\s+20\d{2}\b",
        r"\b\d{1,2}\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+20\d{2}\b",
        r"\b20\d{2}[./-]\d{1,2}[./-]\d{1,2}\b",
        r"\b\d{1,2}[./-]\d{1,2}[./-]20\d{2}\b",
    ]
    for pat in pats:
        m = re.search(pat, text, re.I)
        if m:
            d = parse_date(m.group(0))
            if d:
                return d
    return None


def recent(d: str | None) -> bool:
    if not d:
        return False
    try:
        value = datetime.strptime(d, "%Y-%m-%d").date()
    except Exception:
        return False
    today = datetime.now().date()
    return today - timedelta(days=MAX_AGE_DAYS) <= value <= today + timedelta(days=2)


def pace(url: str) -> None:
    host = urlparse(url).netloc.lower()
    last = LAST_HOST.get(host, 0.0)
    wait = MIN_HOST_INTERVAL - (time.monotonic() - last)
    if wait > 0:
        time.sleep(wait)
    LAST_HOST[host] = time.monotonic()


def fetch(url: str) -> tuple[str, str]:
    pace(url)
    r = SESSION.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT, allow_redirects=True)
    r.raise_for_status()
    return r.text, r.url


class Browser:
    def __init__(self):
        self.pw = None
        self.browser = None

    def get(self, url: str, wait_ms: int = 1500) -> tuple[str, str]:
        if not ENABLE_BROWSER:
            raise RuntimeError("Browser fallback disabled")
        if self.browser is None:
            from playwright.sync_api import sync_playwright

            self.pw = sync_playwright().start()
            self.browser = self.pw.chromium.launch(headless=True)
        pace(url)
        page = self.browser.new_page(user_agent=UA, locale="en-US", viewport={"width": 1440, "height": 1000})
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=BROWSER_TIMEOUT_MS)
            try:
                page.wait_for_load_state("networkidle", timeout=min(8000, BROWSER_TIMEOUT_MS))
            except Exception:
                pass
            page.wait_for_timeout(wait_ms)
            return page.content(), page.url
        finally:
            page.close()

    def close(self) -> None:
        if self.browser:
            self.browser.close()
        if self.pw:
            self.pw.stop()


def fetch_page(url: str, source: dict, browser: Browser) -> tuple[str, str, str]:
    err = None
    markup = final = None
    try:
        markup, final = fetch(url)
    except Exception as exc:
        err = exc
    threshold = int(source.get("render_if_text_lt", 0) or 0)
    text_len = len(clean(BeautifulSoup(markup, "html.parser").get_text(" ", strip=True))) if markup else 0
    use_browser = source.get("browser_fallback") and ENABLE_BROWSER and (
        source.get("force_browser") or err is not None or (threshold and text_len < threshold)
    )
    if use_browser:
        try:
            m, u = browser.get(url, int(source.get("browser_wait_ms", 1500)))
            return m, u, "browser"
        except Exception as b_exc:
            if markup is None:
                raise RuntimeError(f"requests={err}; browser={b_exc}")
    if markup is not None:
        return markup, final, "requests"
    raise err or RuntimeError("fetch failed")


def score(url: str, title: str, context: str, source: dict, date_hint: str | None = None) -> int:
    if not article_url(url, source):
        return -100
    s = 0
    if is_wind(title):
        s += 8
    if is_wind(context):
        s += 4
    if is_wind(url.replace("-", " ")):
        s += 2
    if date_hint:
        s += 2
    if any(h.lower() in urlparse(url).path.lower() for h in source.get("article_path_hints", [])):
        s += 1
    if source.get("assume_wind_listing"):
        s += 3
    if source.get("broad_discovery") and (date_hint or s >= 1):
        s = max(s, 3)
    return s


def discover_html(
    markup: str,
    base_url: str,
    source: dict,
    default_date: str | None = None,
) -> list[dict]:
    soup = BeautifulSoup(markup, "html.parser")
    for x in soup.select("script,style,noscript,svg,footer"):
        x.decompose()
    found: dict[str, dict] = {}
    for a in soup.find_all("a", href=True):
        title = clean(a.get_text(" ", strip=True) or a.get("title") or a.get("aria-label"))
        if len(title) < 6 or len(title) > 360:
            continue
        raw_url = urljoin(base_url, a.get("href", ""))
        url = fix_source_url(raw_url, source)
        if not url or not same_host(base_url, url):
            continue
        c = a
        for _ in range(3):
            if not getattr(c, "parent", None):
                break
            c = c.parent
        context = clean(c.get_text(" ", strip=True))[:1800]
        d_hint = date_text(context) or date_url(url) or default_date
        sc = score(url, title, context, source, d_hint)
        if sc < 3:
            continue
        item = {
            "url": url,
            "date_hint": d_hint,
            "score": sc,
            "title_hint": title,
            "context_hint": context[:700],
        }
        if url not in found or sc > found[url].get("score", 0):
            found[url] = item
    out = list(found.values())
    out.sort(
        key=lambda x: (
            1 if recent(x.get("date_hint")) else 0,
            x.get("date_hint") or "",
            x.get("score", 0),
        ),
        reverse=True,
    )
    return out[:MAX_CANDIDATES]


def discover_feed(feed_url: str, source: dict) -> list[dict]:
    parsed = feedparser.parse(feed_url, request_headers=HEADERS)
    out = []
    for e in parsed.entries[:80]:
        title = clean(e.get("title", ""))
        summary = clean(e.get("summary", ""))
        url = fix_source_url(e.get("link", ""), source)
        d_hint = parse_date(e.get("published") or e.get("updated")) or date_url(url)
        if title and url and article_url(url, source) and (
            is_wind(f"{title} {summary} {url}") or source.get("assume_wind_listing")
        ):
            out.append(
                {
                    "url": url,
                    "date_hint": d_hint,
                    "score": 12 if is_wind(title) else 7,
                    "title_hint": title,
                    "context_hint": summary[:700],
                }
            )
    return out[:MAX_CANDIDATES]


def jsonld(soup: BeautifulSoup) -> tuple[list[str], list[str]]:
    dates: list[str] = []
    heads: list[str] = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or script.get_text())
        except Exception:
            continue

        def walk(o):
            if isinstance(o, dict):
                for k, v in o.items():
                    if k in {"datePublished", "dateCreated", "uploadDate"} and isinstance(v, str):
                        dates.append(v)
                    elif k in {"headline", "name"} and isinstance(v, str) and len(v) > 8:
                        heads.append(v)
                    else:
                        walk(v)
            elif isinstance(o, list):
                for i in o:
                    walk(i)

        walk(data)
    return dates, heads


def paragraphs(soup: BeautifulSoup) -> list[str]:
    root = soup.find("article") or soup.find("main")
    if root is None:
        candidates = soup.select("[class*='article'],[class*='story'],[class*='content'],[class*='post']")
        root = max(candidates, key=lambda x: len(clean(x.get_text(" ", strip=True))), default=soup.body)
    if root is None:
        return []
    out: list[str] = []
    for p in root.find_all(["p", "h2"]):
        txt = clean(p.get_text(" ", strip=True))
        if len(txt) < 45:
            continue
        cls = " ".join(p.get("class", [])).lower()
        if any(x in cls for x in ["cookie", "share", "social", "related", "newsletter", "subscribe", "advert"]):
            continue
        out.append(txt)
        if len(" ".join(out)) >= MAX_EXCERPT_CHARS * 2:
            break
    return out


def make_excerpt(parts: list[str], fallback: str = "") -> str:
    out: list[str] = []
    total = 0
    for p in parts:
        room = MAX_EXCERPT_CHARS - total
        if room <= 0:
            break
        out.append(p[:room])
        total += len(out[-1]) + 1
        if len(out) >= 2:
            break
    return "\n\n".join(out).strip() or clean(fallback)[:MAX_EXCERPT_CHARS]


def clean_title(title: str, source: dict) -> str:
    title = clean(title)
    if source.get("id") == "asean_wind_expo":
        title = re.sub(r"[_|]\s*(?:Newsletter|ASEAN).*?$", "", title, flags=re.I)
    if source.get("id") == "qazaqgreen":
        title = re.sub(r"\s*[|–—-]\s*QazaqGreen.*$", "", title, flags=re.I)
    return clean(title)


def bad_title(title: str, source: dict | None = None) -> bool:
    t = clean(title).strip(" -–—|:_").lower()
    if not t or len(t) < 8:
        return True
    if t in BAD_TITLES:
        return True
    if source and t == clean(source.get("name", "")).lower():
        return True
    return False


def relevant_article(title: str, desc: str, body: str, url: str, source: dict) -> bool:
    if source.get("strict_wind"):
        lead = f"{title} {desc} {body[:1400]} {url}"
        return is_wind(lead)
    return is_wind(f"{title} {desc} {body[:5000]} {url}") or source.get("assume_wind_listing", False)


def extract_article(url: str, source: dict, browser: Browser, candidate: dict | None = None) -> dict | None:
    candidate = candidate or {}
    markup, final, mode = fetch_page(url, source, browser)
    soup = BeautifulSoup(markup, "html.parser")
    dates, heads = jsonld(soup)

    title = ""
    for sel in ['meta[property="og:title"]', 'meta[name="twitter:title"]']:
        tag = soup.select_one(sel)
        if tag and tag.get("content"):
            title = clean(tag.get("content"))
            break
    if not title and heads:
        title = clean(heads[0])
    if not title:
        h1 = soup.find("h1")
        title = clean(h1.get_text(" ", strip=True) if h1 else "")
    if not title and soup.title:
        title = clean(soup.title.get_text(" ", strip=True))
    title = clean_title(title, source)
    if bad_title(title, source) and candidate.get("title_hint"):
        title = clean_title(candidate["title_hint"], source)
    if bad_title(title, source):
        return None

    desc = ""
    for sel in ['meta[property="og:description"]', 'meta[name="description"]']:
        tag = soup.select_one(sel)
        if tag and tag.get("content"):
            desc = clean(tag.get("content"))
            break

    parts = paragraphs(soup)
    body = clean(" ".join(parts))[:14000]
    if not relevant_article(title, desc, body, final, source):
        return None

    published = None
    raw_dates = list(dates)
    for sel, attr in [
        ('meta[property="article:published_time"]', "content"),
        ('meta[name="date"]', "content"),
        ('meta[name="pubdate"]', "content"),
        ('meta[itemprop="datePublished"]', "content"),
        ('time[datetime]', "datetime"),
    ]:
        for tag in soup.select(sel)[:8]:
            if tag.get(attr):
                raw_dates.append(tag.get(attr))
    for raw in raw_dates:
        published = parse_date(str(raw))
        if published:
            break
    published = published or candidate.get("date_hint") or date_text(body[:4500]) or date_text(soup.get_text(" ", strip=True)[:5000]) or date_url(final)
    if not recent(published):
        return None

    canonical = soup.select_one('link[rel="canonical"]')
    article = fix_source_url(urljoin(final, canonical.get("href")), source) if canonical and canonical.get("href") else fix_source_url(final, source)
    if not article_url(article, source):
        article = fix_source_url(final, source)
    if not article_url(article, source):
        article = fix_source_url(url, source)
    if not article_url(article, source):
        return None

    return {
        "country": source["country"],
        "source_name": source["name"],
        "role": source.get("role", ""),
        "title": title,
        "published_at": published,
        "url": article,
        "excerpt": make_excerpt(parts, desc or candidate.get("context_hint", "")),
        "fetch_mode": mode,
        "fetched_at": now_iso(),
    }


def listing_fallback(candidate: dict, source: dict) -> dict | None:
    if not source.get("listing_fallback"):
        return None
    title = clean_title(candidate.get("title_hint", ""), source)
    d = candidate.get("date_hint")
    url = fix_source_url(candidate.get("url", ""), source)
    if bad_title(title, source) or not recent(d) or not article_url(url, source):
        return None
    if not (is_wind(f"{title} {candidate.get('context_hint','')} {url}") or source.get("assume_wind_listing")):
        return None
    return {
        "country": source["country"],
        "source_name": source["name"],
        "role": source.get("role", ""),
        "title": title,
        "published_at": d,
        "url": url,
        "excerpt": clean(candidate.get("context_hint", ""))[:MAX_EXCERPT_CHARS],
        "fetch_mode": "listing-fallback",
        "fetched_at": now_iso(),
    }


def recent_archive_day_links(markup: str, base_url: str, source: dict) -> list[tuple[str, str]]:
    soup = BeautifulSoup(markup, "html.parser")
    rows: list[tuple[int, str, str]] = []
    for a in soup.find_all("a", href=True):
        txt = clean(a.get_text(" ", strip=True))
        if not re.fullmatch(r"[0-3]?\d", txt):
            continue
        try:
            day = int(txt)
        except ValueError:
            continue
        url = fix_source_url(urljoin(base_url, a["href"]), source)
        d = date_url(url)
        if url and d and recent(d):
            rows.append((day, url, d))
    rows.sort(key=lambda x: x[0], reverse=True)
    limit = int(source.get("archive_days_limit", 10))
    seen = set()
    out = []
    for _, url, d in rows:
        if url in seen:
            continue
        seen.add(url)
        out.append((url, d))
        if len(out) >= limit:
            break
    return out


def collect(source: dict, browser: Browser) -> tuple[list[dict], list[dict]]:
    found: dict[str, dict] = {}
    attempts: list[dict] = []

    def add(items: list[dict]):
        for i in items:
            u = i.get("url")
            if u and (u not in found or i.get("score", 0) > found[u].get("score", 0)):
                found[u] = i

    for raw in source.get("feed_urls", []):
        url = expand_template(raw)
        try:
            items = discover_feed(url, source)
            add(items)
            attempts.append({"strategy": "rss", "url": url, "ok": True, "candidates": len(items)})
        except Exception as e:
            attempts.append({"strategy": "rss", "url": url, "ok": False, "error": f"{type(e).__name__}: {e}"})

    for raw in source.get("entry_urls", []):
        url = expand_template(raw)
        try:
            page, final, mode = fetch_page(url, source, browser)
            if source.get("archive_expand_days"):
                day_links = recent_archive_day_links(page, final, source)
                attempts.append({"strategy": f"archive-month:{mode}", "url": url, "ok": True, "candidates": len(day_links)})
                for day_url, day_date in day_links:
                    try:
                        dpage, dfinal, dmode = fetch_page(day_url, source, browser)
                        items = discover_html(dpage, dfinal, source, default_date=day_date)
                        add(items)
                        attempts.append({"strategy": f"archive-day:{dmode}", "url": day_url, "ok": True, "candidates": len(items)})
                    except Exception as e:
                        attempts.append({"strategy": "archive-day", "url": day_url, "ok": False, "error": f"{type(e).__name__}: {e}"})
            else:
                items = discover_html(page, final, source)
                add(items)
                attempts.append({"strategy": f"entry:{mode}", "url": url, "ok": True, "candidates": len(items)})
        except Exception as e:
            attempts.append({"strategy": "entry", "url": url, "ok": False, "error": f"{type(e).__name__}: {e}"})

    for tpl in source.get("search_urls", []):
        for term in source.get("search_terms", [])[:3]:
            url = expand_template(tpl, query=term)
            try:
                page, final, mode = fetch_page(url, source, browser)
                items = discover_html(page, final, source)
                add(items)
                attempts.append({"strategy": f"search:{mode}", "url": url, "ok": True, "candidates": len(items)})
            except Exception as e:
                attempts.append({"strategy": "search", "url": url, "ok": False, "error": f"{type(e).__name__}: {e}"})

    ranked = list(found.values())
    ranked.sort(
        key=lambda x: (
            1 if recent(x.get("date_hint")) else 0,
            x.get("date_hint") or "",
            x.get("score", 0),
        ),
        reverse=True,
    )
    return ranked[: max(MAX_CANDIDATES, MAX_ARTICLES_PER_SOURCE * 2)], attempts


def scrape_source(source: dict, browser: Browser) -> tuple[list[dict], list[dict]]:
    candidates, attempts = collect(source, browser)
    articles: dict[str, dict] = {}
    for c in candidates:
        if len(articles) >= MAX_ARTICLES_PER_SOURCE:
            break
        try:
            item = extract_article(c["url"], source, browser, c)
        except Exception as e:
            log.debug("detail failed %s: %s", c.get("url"), e)
            item = None
        if item is None:
            item = listing_fallback(c, source)
        if item:
            articles[item["url"]] = item
    items = sorted(articles.values(), key=lambda x: x["published_at"], reverse=True)
    return items, attempts


def load_news() -> list[dict]:
    try:
        data = json.loads(NEWS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def google_translate_http(text: str) -> str:
    params = {
        "client": "gtx",
        "sl": "auto",
        "tl": "zh-CN",
        "dt": "t",
        "q": text,
    }
    r = SESSION.get(
        "https://translate.googleapis.com/translate_a/single",
        params=params,
        headers={"User-Agent": UA, "Accept": "application/json,text/plain,*/*"},
        timeout=TRANSLATE_TIMEOUT,
    )
    r.raise_for_status()
    data = r.json()
    parts = []
    if isinstance(data, list) and data and isinstance(data[0], list):
        for row in data[0]:
            if isinstance(row, list) and row and isinstance(row[0], str):
                parts.append(row[0])
    return clean("".join(parts))


def translate_title(text: str) -> str:
    text = clean(text)
    if not text or not ENABLE_TRANSLATION:
        return ""
    if re.search(r"[\u4e00-\u9fff]", text):
        return text
    if text in TRANSLATION_CACHE:
        return TRANSLATION_CACHE[text]
    result = ""
    try:
        result = google_translate_http(text)
    except Exception as e:
        log.warning("google endpoint translation failed: %s", e)
    if not result:
        try:
            result = clean(GoogleTranslator(source="auto", target="zh-CN").translate(text))
        except Exception as e:
            log.warning("deep-translator failed: %s", e)
    TRANSLATION_CACHE[text] = result
    if result:
        time.sleep(0.12)
    return result


def valid_existing_item(x: dict) -> bool:
    url = clean(x.get("url", ""))
    title = clean(x.get("title", ""))
    source = SOURCE_BY_NAME.get(x.get("source_name", ""), {})
    if not url or bad_title(title, source):
        return False
    # Clean up old V4 records that accidentally stored a homepage/category/search URL.
    if source:
        if not article_url(url, source):
            return False
    else:
        p = urlparse(url)
        low = (p.path or "/").lower()
        if low in ("", "/") or any(x in low for x in BLOCKED):
            return False
    try:
        d = datetime.strptime(x.get("published_at", ""), "%Y-%m-%d").date()
    except Exception:
        return False
    return d >= datetime.now().date() - timedelta(days=MAX_KEEP_DAYS)


def merge(existing: list[dict], fresh: list[dict]) -> list[dict]:
    by: dict[str, dict] = {}
    for x in existing:
        if valid_existing_item(x):
            by[x["url"]] = x
    for item in fresh:
        old = by.get(item["url"], {})
        merged = dict(old)
        merged.update(item)
        # V4.2 no longer translates excerpts; keep old excerpt_zh only if it already exists.
        by[item["url"]] = merged
    out = list(by.values())
    out.sort(key=lambda x: (x.get("published_at", ""), x.get("fetched_at", "")), reverse=True)
    return out[:MAX_KEEP_ARTICLES]


def backfill_title_translations(news: list[dict], budget: int) -> int:
    used = 0
    consecutive_failures = 0
    for item in news:
        if used >= budget:
            break
        if item.get("translated_title"):
            continue
        title = clean(item.get("title", ""))
        if not title:
            continue
        translated = translate_title(title)
        used += 1
        if translated:
            item["translated_title"] = translated
            consecutive_failures = 0
        else:
            consecutive_failures += 1
            if consecutive_failures >= 8:
                log.warning("translation provider failed 8 times consecutively; stop this run")
                break
    return used


def save_csv(news: list[dict]) -> None:
    with CSV_FILE.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["国家/地区", "来源", "原标题", "中文标题", "发布日期", "原文链接"])
        for x in news:
            w.writerow([
                x.get("country", ""),
                x.get("source_name", ""),
                x.get("title", ""),
                x.get("translated_title", ""),
                x.get("published_at", ""),
                x.get("url", ""),
            ])


def main() -> None:
    started = now_iso()
    existing = load_news()
    browser = Browser()
    fresh: list[dict] = []
    statuses: list[dict] = []
    try:
        for idx, source in enumerate(SOURCES, 1):
            log.info("[%d/%d] %s", idx, len(SOURCES), source["name"])
            attempts: list[dict] = []
            try:
                items, attempts = scrape_source(source, browser)
                fresh.extend(items)
                any_ok = any(a.get("ok") for a in attempts)
                status = "success" if items else ("no_news" if any_ok else "error")
                statuses.append(
                    {
                        "id": source["id"],
                        "country": source["country"],
                        "name": source["name"],
                        "role": source.get("role", ""),
                        "adapter": source.get("adapter", ""),
                        "status": status,
                        "found_count": len(items),
                        "checked_at": now_iso(),
                        "error": "" if status != "error" else "所有配置入口均未成功读取。",
                        "attempts": attempts,
                    }
                )
            except Exception as e:
                statuses.append(
                    {
                        "id": source["id"],
                        "country": source["country"],
                        "name": source["name"],
                        "role": source.get("role", ""),
                        "adapter": source.get("adapter", ""),
                        "status": "error",
                        "found_count": 0,
                        "checked_at": now_iso(),
                        "error": f"{type(e).__name__}: {e}",
                        "attempts": attempts,
                    }
                )
    finally:
        browser.close()

    news = merge(existing, fresh)
    translations_used = backfill_title_translations(news, MAX_TRANSLATIONS)

    NEWS_FILE.write_text(json.dumps(news, ensure_ascii=False, indent=2), encoding="utf-8")
    STATUS_FILE.write_text(json.dumps(statuses, ensure_ascii=False, indent=2), encoding="utf-8")
    save_csv(news)
    counts = {k: sum(1 for s in statuses if s["status"] == k) for k in ("success", "no_news", "error")}
    META_FILE.write_text(
        json.dumps(
            {
                "started_at": started,
                "finished_at": now_iso(),
                "source_total": len(SOURCES),
                "success_sources": counts["success"],
                "no_news_sources": counts["no_news"],
                "error_sources": counts["error"],
                "fresh_articles_found": len(fresh),
                "total_articles_kept": len(news),
                "title_translations_attempted": translations_used,
                "schedule_note": "GitHub Actions 每日 00:00 UTC 触发，约等于北京时间 08:00；实际时间可能因排队略有延迟。",
                "version": "4.2",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    log.info("done fresh=%d total=%d translations=%d statuses=%s", len(fresh), len(news), translations_used, counts)


if __name__ == "__main__":
    main()
