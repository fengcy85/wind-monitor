from __future__ import annotations

import csv, json, logging, os, re, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, quote_plus, urlencode, urljoin, urlparse, urlunparse

import dateparser, feedparser, requests
from bs4 import BeautifulSoup
from deep_translator import GoogleTranslator
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE = Path(__file__).resolve().parent
DATA = BASE / "site" / "data"
DATA.mkdir(parents=True, exist_ok=True)
SOURCES = json.loads((BASE / "sources.json").read_text(encoding="utf-8"))

NEWS_FILE = DATA / "news.json"
STATUS_FILE = DATA / "source_status.json"
META_FILE = DATA / "meta.json"
CSV_FILE = DATA / "wind_news.csv"

MAX_AGE_DAYS = int(os.getenv("MAX_AGE_DAYS", "120"))
MAX_KEEP_DAYS = int(os.getenv("MAX_KEEP_DAYS", "730"))
MAX_KEEP_ARTICLES = int(os.getenv("MAX_KEEP_ARTICLES", "1500"))
MAX_ARTICLES_PER_SOURCE = int(os.getenv("MAX_ARTICLES_PER_SOURCE", "20"))
MAX_CANDIDATES = int(os.getenv("MAX_CANDIDATES_PER_ENTRY", "28"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "20"))
MIN_HOST_INTERVAL = float(os.getenv("MIN_HOST_INTERVAL_SECONDS", "1.0"))
ENABLE_BROWSER = os.getenv("ENABLE_BROWSER_FALLBACK", "1") == "1"
BROWSER_TIMEOUT_MS = int(os.getenv("BROWSER_TIMEOUT_MS", "30000"))
MAX_EXCERPT_CHARS = int(os.getenv("MAX_EXCERPT_CHARS", "900"))
MAX_EXCERPT_ZH_CHARS = int(os.getenv("MAX_EXCERPT_ZH_CHARS", "650"))
ENABLE_TRANSLATION = os.getenv("ENABLE_TRANSLATION", "1") == "1"
MAX_TRANSLATIONS = int(os.getenv("MAX_TRANSLATIONS_PER_RUN", "100"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("wind-monitor-v4")

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126 Safari/537.36 WindPolicyMonitor/4.0"
HEADERS = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.7"}
SESSION = requests.Session()
SESSION.mount("https://", HTTPAdapter(max_retries=Retry(
    total=2, connect=2, read=2, backoff_factor=.7,
    status_forcelist=[429,500,502,503,504], allowed_methods=frozenset(["GET"])
)))

WIND_TERMS = [
    "wind","wind power","wind energy","wind farm","windfarm","offshore","onshore",
    "turbine","turbines","repowering","eólica","eolica","eólico","eolico",
    "energia eólica","energia eolica","éolien","éolienne","windkraft","windenergie",
    "rüzgar","ruzgar","ветр","ветро","жел энергиясы","điện gió","dien gio",
    "gió ngoài khơi","طاقة الرياح","رياح","shamol"
]
BLOCKED = ["/category/","/tag/","/tags/","/author/","/search","/privacy","/cookie",
           "/about","/contact","/login","/register","/feed/"]
SKIP_EXT = (".jpg",".jpeg",".png",".gif",".svg",".webp",".pdf",".zip",".doc",
            ".docx",".xls",".xlsx",".mp4",".mp3",".ico")
LAST_HOST = {}

def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def clean(v):
    return re.sub(r"\s+", " ", v or "").strip()

def is_wind(text):
    low = clean(text).lower()
    return any(t in low for t in WIND_TERMS)

def norm(url):
    try:
        p = urlparse(url)
        if p.scheme not in ("http","https"):
            return ""
        q = [(k,v) for k,v in parse_qsl(p.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in {"fbclid","gclid"}]
        return urlunparse((p.scheme,p.netloc.lower(),re.sub(r"/{2,}","/",p.path or "/"),"",urlencode(q),""))
    except Exception:
        return ""

def same_host(a,b):
    return urlparse(a).netloc.lower().removeprefix("www.") == urlparse(b).netloc.lower().removeprefix("www.")

def expand(url):
    now = datetime.now()
    months = ["january","february","march","april","may","june","july","august",
              "september","october","november","december"]
    return url.format(year=now.year, month=now.month, day=now.day, month_name_lower=months[now.month-1])

def entry_urls(source):
    return {norm(expand(u)) for u in source.get("entry_urls",[])}

def article_url(url, source):
    url = norm(url)
    if not url or url in entry_urls(source):
        return False
    p = urlparse(url)
    low = (p.path or "/").lower()
    if low in ("","/") or any(x in low for x in BLOCKED) or url.lower().endswith(SKIP_EXT):
        return False
    q = dict(parse_qsl(p.query))
    if any(k.lower() in {"id","article","newsid","story","post"} and str(v).strip() for k,v in q.items()):
        return True
    seg = [x for x in p.path.split("/") if x]
    if re.search(r"/20\d{2}/", p.path):
        return True
    if any(h.lower() in low for h in source.get("article_path_hints",[])) and len(seg) >= 2:
        return True
    return len(seg) >= 3

def parse_date(raw):
    raw = clean(raw)
    if not raw:
        return None
    try:
        dt = dateparser.parse(raw, settings={"PREFER_DATES_FROM":"past","RETURN_AS_TIMEZONE_AWARE":False,"DATE_ORDER":"DMY"})
        return dt.date().isoformat() if dt else None
    except Exception:
        return None

def date_url(url):
    for pat in (r"/(20\d{2})/(\d{1,2})/(\d{1,2})(?:/|$)", r"/(20\d{2})-(\d{1,2})-(\d{1,2})(?:/|$)"):
        m = re.search(pat,url)
        if m:
            try:
                return datetime(*map(int,m.groups())).date().isoformat()
            except Exception:
                pass
    return None

def date_text(text):
    text = clean(text)[:2600]
    pats = [
        r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},?\s+20\d{2}\b",
        r"\b\d{1,2}\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+20\d{2}\b",
        r"\b20\d{2}[./-]\d{1,2}[./-]\d{1,2}\b",
        r"\b\d{1,2}[./-]\d{1,2}[./-]20\d{2}\b",
    ]
    for pat in pats:
        m = re.search(pat,text,re.I)
        if m:
            d = parse_date(m.group(0))
            if d:
                return d
    return None

def recent(d):
    if not d:
        return False
    try:
        value = datetime.strptime(d,"%Y-%m-%d").date()
    except Exception:
        return False
    today = datetime.now().date()
    return today - timedelta(days=MAX_AGE_DAYS) <= value <= today + timedelta(days=2)

def pace(url):
    host = urlparse(url).netloc.lower()
    last = LAST_HOST.get(host,0)
    wait = MIN_HOST_INTERVAL - (time.monotonic()-last)
    if wait > 0:
        time.sleep(wait)
    LAST_HOST[host] = time.monotonic()

def fetch(url):
    pace(url)
    r = SESSION.get(url,headers=HEADERS,timeout=REQUEST_TIMEOUT,allow_redirects=True)
    r.raise_for_status()
    return r.text,r.url

class Browser:
    def __init__(self):
        self.pw = self.browser = None
    def get(self,url,wait_ms=1500):
        if not ENABLE_BROWSER:
            raise RuntimeError("Browser fallback disabled")
        if self.browser is None:
            from playwright.sync_api import sync_playwright
            self.pw = sync_playwright().start()
            self.browser = self.pw.chromium.launch(headless=True)
        pace(url)
        page = self.browser.new_page(user_agent=UA,locale="en-US",viewport={"width":1440,"height":1000})
        try:
            page.goto(url,wait_until="domcontentloaded",timeout=BROWSER_TIMEOUT_MS)
            try:
                page.wait_for_load_state("networkidle",timeout=min(7000,BROWSER_TIMEOUT_MS))
            except Exception:
                pass
            page.wait_for_timeout(wait_ms)
            return page.content(),page.url
        finally:
            page.close()
    def close(self):
        if self.browser:
            self.browser.close()
        if self.pw:
            self.pw.stop()

def fetch_page(url,source,browser):
    err = None
    markup = final = None
    try:
        markup,final = fetch(url)
    except Exception as exc:
        err = exc
    threshold = int(source.get("render_if_text_lt",0) or 0)
    text_len = len(clean(BeautifulSoup(markup,"html.parser").get_text(" ",strip=True))) if markup else 0
    use_browser = source.get("browser_fallback") and ENABLE_BROWSER and (
        source.get("force_browser") or err is not None or (threshold and text_len < threshold)
    )
    if use_browser:
        try:
            m,u = browser.get(url,int(source.get("browser_wait_ms",1500)))
            return m,u,"browser"
        except Exception as b_exc:
            if markup is None:
                raise RuntimeError(f"requests={err}; browser={b_exc}")
    if markup is not None:
        return markup,final,"requests"
    raise err or RuntimeError("fetch failed")

def score(url,title,context,source):
    if not article_url(url,source):
        return -100
    s = 0
    if is_wind(title): s += 7
    if is_wind(context): s += 3
    if is_wind(url.replace("-"," ")): s += 2
    if date_text(context): s += 1
    if re.search(r"/20\d{2}/",url): s += 2
    return s

def discover_html(markup,base_url,source):
    soup = BeautifulSoup(markup,"html.parser")
    for x in soup.select("script,style,noscript,svg,footer"):
        x.decompose()
    found = {}
    for a in soup.find_all("a",href=True):
        title = clean(a.get_text(" ",strip=True))
        if len(title) < 10 or len(title) > 320:
            continue
        url = norm(urljoin(base_url,a.get("href","")))
        if not url or not same_host(base_url,url):
            continue
        c = a
        for _ in range(3):
            if not getattr(c,"parent",None): break
            c = c.parent
        context = clean(c.get_text(" ",strip=True))[:1500]
        sc = score(url,title,context,source)
        if sc < 3:
            continue
        item = {"url":url,"date_hint":date_text(context) or date_url(url),"score":sc}
        if url not in found or sc > found[url]["score"]:
            found[url] = item
    out = list(found.values())
    out.sort(key=lambda x:(1 if recent(x.get("date_hint")) else 0,x.get("date_hint") or "",x["score"]),reverse=True)
    return out[:MAX_CANDIDATES]

def discover_feed(feed_url,source):
    parsed = feedparser.parse(feed_url,request_headers=HEADERS)
    out = []
    for e in parsed.entries[:70]:
        title = clean(e.get("title",""))
        summary = clean(e.get("summary",""))
        url = norm(e.get("link",""))
        if title and url and article_url(url,source) and is_wind(f"{title} {summary} {url}"):
            out.append({"url":url,"date_hint":parse_date(e.get("published") or e.get("updated")) or date_url(url),
                        "score":11 if is_wind(title) else 7})
    return out[:MAX_CANDIDATES]

def jsonld(soup):
    dates,heads = [],[]
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or script.get_text())
        except Exception:
            continue
        def walk(o):
            if isinstance(o,dict):
                for k,v in o.items():
                    if k in {"datePublished","dateCreated","uploadDate"} and isinstance(v,str): dates.append(v)
                    elif k in {"headline","name"} and isinstance(v,str) and len(v)>8: heads.append(v)
                    else: walk(v)
            elif isinstance(o,list):
                for i in o: walk(i)
        walk(data)
    return dates,heads

def paragraphs(soup):
    root = soup.find("article") or soup.find("main")
    if root is None:
        candidates = soup.select("[class*='article'],[class*='story'],[class*='content'],[class*='post']")
        root = max(candidates,key=lambda x:len(clean(x.get_text(" ",strip=True))),default=soup.body)
    if root is None:
        return []
    out = []
    for p in root.find_all(["p","h2"]):
        txt = clean(p.get_text(" ",strip=True))
        if len(txt) < 45:
            continue
        cls = " ".join(p.get("class",[])).lower()
        if any(x in cls for x in ["cookie","share","social","related","newsletter","subscribe","advert"]):
            continue
        out.append(txt)
        if len(" ".join(out)) >= MAX_EXCERPT_CHARS*2:
            break
    return out

def make_excerpt(parts,fallback=""):
    out,total = [],0
    for p in parts:
        room = MAX_EXCERPT_CHARS-total
        if room <= 0: break
        out.append(p[:room]); total += len(out[-1])+1
        if len(out) >= 3: break
    return "\n\n".join(out).strip() or clean(fallback)[:MAX_EXCERPT_CHARS]

def extract_article(url,source,browser,date_hint=None):
    markup,final,mode = fetch_page(url,source,browser)
    soup = BeautifulSoup(markup,"html.parser")
    dates,heads = jsonld(soup)

    title = ""
    for sel in ['meta[property="og:title"]','meta[name="twitter:title"]']:
        tag = soup.select_one(sel)
        if tag and tag.get("content"):
            title = clean(tag.get("content")); break
    if not title and heads: title = clean(heads[0])
    if not title:
        h1 = soup.find("h1"); title = clean(h1.get_text(" ",strip=True) if h1 else "")
    if not title and soup.title: title = clean(soup.title.get_text(" ",strip=True))
    if len(title) < 8: return None

    desc = ""
    for sel in ['meta[property="og:description"]','meta[name="description"]']:
        tag = soup.select_one(sel)
        if tag and tag.get("content"):
            desc = clean(tag.get("content")); break

    parts = paragraphs(soup)
    body = clean(" ".join(parts))[:14000]
    if not is_wind(f"{title} {desc} {body[:6000]} {final}"):
        return None

    published = None
    raw_dates = list(dates)
    for sel,attr in [('meta[property="article:published_time"]',"content"),('meta[name="date"]',"content"),
                     ('meta[name="pubdate"]',"content"),('meta[itemprop="datePublished"]',"content"),('time[datetime]',"datetime")]:
        for tag in soup.select(sel)[:8]:
            if tag.get(attr): raw_dates.append(tag.get(attr))
    for raw in raw_dates:
        published = parse_date(str(raw))
        if published: break
    published = published or date_hint or date_text(body[:4000]) or date_url(final)
    if not recent(published):
        return None

    canonical = soup.select_one('link[rel="canonical"]')
    article = norm(urljoin(final,canonical.get("href"))) if canonical and canonical.get("href") else norm(final)
    if not article_url(article,source):
        article = norm(final)
    if not article_url(article,source):
        return None

    return {"country":source["country"],"source_name":source["name"],"role":source.get("role",""),
            "title":title,"published_at":published,"url":article,
            "excerpt":make_excerpt(parts,desc),"fetch_mode":mode,"fetched_at":now_iso()}

def collect(source,browser):
    found,attempts = {},[]
    def add(items):
        for i in items:
            u=i.get("url")
            if u and (u not in found or i.get("score",0)>found[u].get("score",0)):
                found[u]=i
    for raw in source.get("feed_urls",[]):
        url=expand(raw)
        try:
            items=discover_feed(url,source); add(items)
            attempts.append({"strategy":"rss","url":url,"ok":True,"candidates":len(items)})
        except Exception as e:
            attempts.append({"strategy":"rss","url":url,"ok":False,"error":f"{type(e).__name__}: {e}"})
    for raw in source.get("entry_urls",[]):
        url=expand(raw)
        try:
            page,final,mode=fetch_page(url,source,browser); items=discover_html(page,final,source); add(items)
            attempts.append({"strategy":f"entry:{mode}","url":url,"ok":True,"candidates":len(items)})
        except Exception as e:
            attempts.append({"strategy":"entry","url":url,"ok":False,"error":f"{type(e).__name__}: {e}"})
    for tpl in source.get("search_urls",[]):
        for term in source.get("search_terms",[])[:2]:
            url=expand(tpl).replace("{query}",quote_plus(term))
            try:
                page,final,mode=fetch_page(url,source,browser); items=discover_html(page,final,source); add(items)
                attempts.append({"strategy":f"search:{mode}","url":url,"ok":True,"candidates":len(items)})
            except Exception as e:
                attempts.append({"strategy":"search","url":url,"ok":False,"error":f"{type(e).__name__}: {e}"})
    ranked=list(found.values())
    ranked.sort(key=lambda x:(1 if recent(x.get("date_hint")) else 0,x.get("date_hint") or "",x.get("score",0)),reverse=True)
    return ranked[:max(MAX_CANDIDATES,MAX_ARTICLES_PER_SOURCE*2)],attempts

def scrape_source(source,browser):
    candidates,attempts=collect(source,browser)
    articles={}
    for c in candidates:
        if len(articles)>=MAX_ARTICLES_PER_SOURCE: break
        try:
            item=extract_article(c["url"],source,browser,c.get("date_hint"))
            if item: articles[item["url"]]=item
        except Exception as e:
            log.debug("detail failed %s: %s",c.get("url"),e)
    items=sorted(articles.values(),key=lambda x:x["published_at"],reverse=True)
    return items,attempts

def load_news():
    try:
        data=json.loads(NEWS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data,list) else []
    except Exception:
        return []

def translate(text,limit=None):
    text=clean(text)
    if not text or not ENABLE_TRANSLATION: return ""
    if limit: text=text[:limit]
    if re.search(r"[\u4e00-\u9fff]",text): return text
    try:
        return clean(GoogleTranslator(source="auto",target="zh-CN").translate(text))
    except Exception as e:
        log.warning("translation failed: %s",e); return ""

def merge(existing,fresh,budget):
    by={x.get("url"):x for x in existing if x.get("url")}
    for item in fresh:
        old=by.get(item["url"],{})
        merged=dict(old); merged.update(item)
        if not merged.get("translated_title") and budget[0]>0:
            merged["translated_title"]=translate(merged.get("title",""),500); budget[0]-=1
        if not merged.get("excerpt_zh") and merged.get("excerpt") and budget[0]>0:
            merged["excerpt_zh"]=translate(merged["excerpt"],MAX_EXCERPT_ZH_CHARS); budget[0]-=1
        by[item["url"]]=merged
    cutoff=datetime.now().date()-timedelta(days=MAX_KEEP_DAYS)
    out=[]
    for x in by.values():
        try: d=datetime.strptime(x.get("published_at",""),"%Y-%m-%d").date()
        except Exception: continue
        if d>=cutoff: out.append(x)
    out.sort(key=lambda x:(x.get("published_at",""),x.get("fetched_at","")),reverse=True)
    return out[:MAX_KEEP_ARTICLES]

def save_csv(news):
    with CSV_FILE.open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.writer(f); w.writerow(["国家/地区","来源","原标题","中文标题","发布日期","原文链接","原文节选","中文翻译（节选）"])
        for x in news:
            w.writerow([x.get("country",""),x.get("source_name",""),x.get("title",""),x.get("translated_title",""),
                        x.get("published_at",""),x.get("url",""),x.get("excerpt",""),x.get("excerpt_zh","")])

def main():
    started=now_iso()
    existing=load_news()
    browser=Browser()
    fresh,statuses=[],[]
    try:
        for idx,source in enumerate(SOURCES,1):
            log.info("[%d/%d] %s",idx,len(SOURCES),source["name"])
            attempts=[]
            try:
                items,attempts=scrape_source(source,browser); fresh.extend(items)
                status="success" if items else ("no_news" if any(a.get("ok") for a in attempts) else "error")
                statuses.append({"id":source["id"],"country":source["country"],"name":source["name"],
                                 "role":source.get("role",""),"adapter":source.get("adapter",""),
                                 "status":status,"found_count":len(items),"checked_at":now_iso(),
                                 "error":"" if status!="error" else "所有配置入口均未成功读取。","attempts":attempts})
            except Exception as e:
                statuses.append({"id":source["id"],"country":source["country"],"name":source["name"],
                                 "role":source.get("role",""),"adapter":source.get("adapter",""),
                                 "status":"error","found_count":0,"checked_at":now_iso(),
                                 "error":f"{type(e).__name__}: {e}","attempts":attempts})
    finally:
        browser.close()

    news=merge(existing,fresh,[MAX_TRANSLATIONS])
    NEWS_FILE.write_text(json.dumps(news,ensure_ascii=False,indent=2),encoding="utf-8")
    STATUS_FILE.write_text(json.dumps(statuses,ensure_ascii=False,indent=2),encoding="utf-8")
    save_csv(news)

    counts={k:sum(1 for s in statuses if s["status"]==k) for k in ("success","no_news","error")}
    META_FILE.write_text(json.dumps({
        "started_at":started,"finished_at":now_iso(),"source_total":len(SOURCES),
        "success_sources":counts["success"],"no_news_sources":counts["no_news"],
        "error_sources":counts["error"],"fresh_articles_found":len(fresh),
        "total_articles_kept":len(news),
        "schedule_note":"GitHub Actions 每日 00:00 UTC 触发，约等于北京时间 08:00；实际时间可能因排队略有延迟。"
    },ensure_ascii=False,indent=2),encoding="utf-8")
    log.info("done fresh=%d total=%d statuses=%s",len(fresh),len(news),counts)

if __name__=="__main__":
    main()
