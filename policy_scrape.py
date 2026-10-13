"""Wind-monitor policy discovery: date-safe incremental scans and source diagnostics.

Public, unauthenticated official websites only. No CAPTCHA or access-control bypass.
CLI: python policy_scrape.py --days 14  # manual backfill
     python policy_scrape.py --days 2   # daily scan with overlap and URL deduplication
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import ipaddress
import json
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'site' / 'data'
SOURCES = ROOT / 'policy_sources.json'
DIMENSIONS = ROOT / 'regulatory_dimensions.json'
POLICIES = DATA / 'policies.json'
REVIEW = DATA / 'policy_candidates_review.json'
STATUSES = DATA / 'policy_source_status.json'
STATE = DATA / 'policy_crawl_state.json'
CSV_FILE = DATA / 'policies.csv'

LOG = logging.getLogger('wind-policy-v5.2')
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
REQUEST_TIMEOUT = min(25.0, max(2.0, float(os.getenv('POLICY_REQUEST_TIMEOUT', '9'))))
MAX_WORKERS = min(8, max(1, int(os.getenv('POLICY_WORKERS', '5'))))
MAX_DETAILS = min(24, max(1, int(os.getenv('POLICY_DETAILS_PER_SOURCE', '10'))))
SOURCE_BUDGET = min(240.0, max(12.0, float(os.getenv('POLICY_SOURCE_BUDGET_SECONDS', '90'))))
ENABLE_BROWSER = os.getenv('POLICY_BROWSER_FALLBACK', '1') == '1'
MAX_ITEMS, MAX_REVIEW = 1200, 650
USER_AGENT = 'WindMonitorPolicyResearch/5.2 (+public document monitoring; contact via repository)'

WIND = re.compile(r'风力发电|风电|风力机|风电机组|风力发电机组|风能|wind\s+(?:power|energy|farm|turbine|generation)|windfarm|onshore\s+wind|offshore\s+wind|e[oó]lica|[éê]olien', re.I)
POLICY = re.compile(r'条例|法规|法律|办法|规定|规则|通知|意见|标准|规范|政策|技术要求|实施方案|准入|认证|招标|并网|规划|修订|废止|批复|采购|电价|tariff|regulat|standard|decree|law|policy|resolution|rule|grid code|tender', re.I)
CROSS_SECTOR = re.compile(r'电力|新能源|可再生能源|能源|并网|输电|电网|标准|装备|制造|海洋|数据安全|运输|采购|认证|electricity|renewable|grid|energy', re.I)
REJECT_TITLE = re.compile(r'^(首页|更多|更多信息|政策法规|政策文件|信息公开|政策解读|通知公告|联系我们|上一页|下一页|政府信息公开|政务公开|点击查看|Download|Read more)$', re.I)
SKIP_PATH = re.compile(r'\.(?:jpg|jpeg|png|gif|svg|js|css|zip|xlsx?|docx?|mp4|mp3|ico)(?:\?|$)', re.I)
PDF_PATH = re.compile(r'\.pdf(?:\?|$)', re.I)
DATE_RE = re.compile(r'(?<!\d)(20[0-3]\d)\s*[-年/.]\s*(0?[1-9]|1[0-2])\s*[-月/.]\s*(0?[1-9]|[12]\d|3[01])\s*(?:日)?(?!\d)')
COMPACT_DATE_RE = re.compile(r'(?<!\d)(20[0-3]\d)(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?!\d)')
DATE_LABEL = re.compile(r'(?:发布日期|发布时间|公开日期|发布于|发布日|发表日期|发表时间|Date\s*published|Published\s*(?:on)?|Publication\s*date)\s*[\]】：:\s-]{0,12}(.{0,55})', re.I)
GENERIC_DATE_LABEL = re.compile(r'(?<!成文)(?<!实施)(?<!失效)(?<!废止)(?<!生效)(?<!签发)(?<!印发)(?<!执行)(?<!更新)日期\s*[：:]\s*(.{0,45})')
ISSUED_LABEL = re.compile(r'(?:成文日期|发文日期|签发日期|印发日期)\s*[\]】：:\s-]{0,12}(.{0,35})')
EFFECTIVE_LABEL = re.compile(r'(?:生效日期|实施日期|施行日期|执行日期)\s*[\]】：:\s-]{0,12}(.{0,35})')
INACTIVE_RE = re.compile(r'(?:^[\[（(\s]*失效[）)\]\s]*|废止|已失效|有效性\s*[：:]?\s*失效|效力状态\s*[：:]?\s*失效)', re.I)

DIMENSION_TERMS = [
    ['市场准入', '外商投资', '外资准入', '进口许可', '投资准入', '负面清单', 'foreign ownership', 'market access'],
    ['本地化', '原产地', '本地生产', '国产化', '国内制造', 'local content', 'rules of origin'],
    ['标准', '认证', '检验检测', '型式认证', '国家标准', '行业标准', 'iec 61400', '产品技术规范'],
    ['电网接入', '电网建设', '电力接入', '接入系统', '输电', '配套送出', '输变电', 'grid connection'],
    ['并网', '低电压穿越', '高电压穿越', '频率响应', '无功', '电能质量', 'lvrt', 'hvrt', 'grid code'],
    ['电价', '绿证', '可再生能源开发', '新能源发展', '风电开发', '新能源消纳', '购电协议', 'ppa', '市场化交易'],
    ['招投标', '招标', '政府采购', '投标资格', '采购办法', '资格预审', 'tender', 'bidding'],
    ['关税', '税收', '反倾销', '反补贴', '进口税', '出口税', '贸易救济'],
    ['超限运输', '大件运输', '道路运输', '车辆许可', '桥梁承载', '港口吊装'],
    ['环评', '环境影响', '生态保护', '土地使用', '用地', '噪声', '鸟类', '海洋生态'],
    ['航空', '民航', '净空', '限高', '雷达', '军事', '国防', '机场保护'],
    ['台风', '沙尘', '雷电', '盐雾', '耐低温', '抗震', '极端风速', '极端温度'],
    ['网络安全', '数据安全', '数据跨境', '关键基础设施', '远程访问', 'scada'],
    ['安全生产', '职业安全', '消防', '特种作业', '劳动保护', '起重吊装', '电气安全'],
    ['海上风电', '海洋工程', '海域使用', '海事', '海底电缆', 'offshore wind'],
    ['绿色金融', '项目融资', '贷款', '融资担保', '出口信贷', '银行授信', '外汇'],
    ['运维', '退役', '质保', '维修', '可回收', '回收利用', 'decommission', 'operation and maintenance'],
    ['碳足迹', '碳排放', '碳核算', '供应链尽职', 'esg', '绿色采购', '材料溯源'],
]
_tls = threading.local()


def clean(obj) -> str:
    return re.sub(r'\s+', ' ', str(obj or '')).strip()


def read_json(path: Path, fallback):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return fallback


def write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    tmp.replace(path)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def china_today() -> date:
    return (datetime.now(timezone.utc) + timedelta(hours=8)).date()


def safe_http_url(value: str) -> str:
    try:
        p = urlsplit(str(value).strip())
        if p.scheme.lower() not in ('http', 'https') or not p.hostname or p.username or p.password:
            return ''
        host = p.hostname.lower()
        if host in ('localhost', '0.0.0.0') or host.endswith(('.local', '.internal')):
            return ''
        try:
            if not ipaddress.ip_address(host).is_global:
                return ''
        except ValueError:
            pass
        query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                 if not k.lower().startswith('utm_') and k.lower() not in ('fbclid', 'gclid')]
        return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path or '/', urlencode(query), ''))
    except ValueError:
        return ''


def same_public_site(base: str, other: str) -> bool:
    a = (urlsplit(base).hostname or '').lower().removeprefix('www.')
    b = (urlsplit(other).hostname or '').lower().removeprefix('www.')
    return bool(a and b and (a == b or a.endswith('.' + b) or b.endswith('.' + a) or
                             (a.endswith('.www.gov.cn') and b.endswith('.www.gov.cn'))))


def session() -> requests.Session:
    sess = getattr(_tls, 'session', None)
    if sess is None:
        sess = requests.Session()
        sess.headers.update({'User-Agent': USER_AGENT,
                             'Accept': 'text/html,application/xhtml+xml,application/pdf;q=0.8,*/*;q=0.3',
                             'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.6'})
        retry = Retry(total=1, connect=1, read=0, backoff_factor=0.5,
                      status_forcelist=[500, 502, 503, 504], respect_retry_after_header=True)
        adapter = HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=4)
        sess.mount('https://', adapter)
        sess.mount('http://', adapter)
        _tls.session = sess
    return sess


def get_page(url: str) -> tuple[str, str, bool]:
    """Returns decoded HTML or extracted PDF text; caller validates redirect host."""
    with session().get(url, timeout=(min(4, REQUEST_TIMEOUT), REQUEST_TIMEOUT),
                       allow_redirects=True, stream=True) as response:
        response.raise_for_status()
        final_url = safe_http_url(response.url)
        if not final_url:
            raise ValueError('Redirect target is not public HTTP(S)')
        content = response.raw.read(7_000_001, decode_content=True)
        if len(content) > 7_000_000:
            raise ValueError('Document exceeds 7MB crawl budget')
        content_type = response.headers.get('Content-Type', '').lower()
        is_pdf = 'pdf' in content_type or PDF_PATH.search(final_url) or content[:5] == b'%PDF-'
        if is_pdf:
            try:
                from pypdf import PdfReader
                reader = PdfReader(BytesIO(content), strict=False)
                text = '\n'.join((page.extract_text() or '') for page in list(reader.pages)[:8])
                return clean(text)[:22000], final_url, True
            except Exception as exc:
                raise ValueError('PDF text extraction failed (possibly scanned PDF): ' + str(exc)[:90]) from exc
        if 'html' not in content_type and 'text/' not in content_type and not content[:200].lstrip().startswith(b'<'):
            raise ValueError('Unsupported Content-Type: ' + content_type[:80])
        # Government portals sometimes omit a charset or declare ISO-8859-1.
        response._content = content
        if not response.encoding or response.encoding.lower() in ('ascii', 'iso-8859-1'):
            response.encoding = response.apparent_encoding or 'utf-8'
        return response.text, final_url, False


def parse_date(text: str, allow_future: bool = False) -> str:
    raw = clean(text)
    match = DATE_RE.search(raw) or COMPACT_DATE_RE.search(raw)
    if not match:
        # English publications (e.g. IEA) with month names.
        try:
            from dateparser import parse as dateparse
            if len(raw) <= 85 and re.search(r'\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)', raw, re.I):
                parsed = dateparse(raw, languages=['en'], settings={'STRICT_PARSING': True})
                return parsed.date().isoformat() if parsed and 2000 <= parsed.year <= 2039 else ''
        except (ImportError, ValueError, TypeError):
            pass
        return ''
    try:
        d = date(*map(int, match.groups()[:3]))
        if not allow_future and d > china_today() + timedelta(days=1):
            return ''
        return d.isoformat()
    except ValueError:
        return ''


def jsonld_dates(obj):
    if isinstance(obj, list):
        for entry in obj:
            yield from jsonld_dates(entry)
    elif isinstance(obj, dict):
        if obj.get('datePublished'):
            yield str(obj['datePublished'])
        for key in ('@graph', 'mainEntity', 'itemListElement'):
            if key in obj:
                yield from jsonld_dates(obj[key])


def extract_dates(html: str, main_text: str = '', listing_date: str = '') -> dict:
    soup = BeautifulSoup(html, 'html.parser') if html else None
    published, date_from = '', ''
    if soup:
        for tag in soup.find_all('meta'):
            attrs = ' '.join(str(tag.get(a, '')) for a in ('name', 'property', 'itemprop')).lower()
            if any(k in attrs for k in ('datepublished', 'publishdate', 'pubdate', 'published_time', 'release_date', 'publicationdate', 'pubtime')):
                published = parse_date(tag.get('content', ''))
                if published:
                    date_from = '页面元数据'
                    break
        if not published:
            for script in soup.find_all('script', type='application/ld+json')[:10]:
                try:
                    obj = json.loads(script.string or script.get_text())
                    for value in jsonld_dates(obj):
                        published = parse_date(value)
                        if published:
                            date_from = 'JSON-LD'
                            break
                except (ValueError, TypeError):
                    pass
                if published:
                    break
        if not published:
            for elem in soup.find_all('time', limit=12):
                published = parse_date(elem.get('datetime', '') or elem.get_text(' ', strip=True))
                if published:
                    date_from = 'time 标签'
                    break
        if not published:
            for tag in soup.select('.publish-time,.publish_time,.pubtime,.pubDate,.date,.pub-date,.time,.release-date')[:12]:
                snippet = clean(tag.get_text(' ', strip=True))
                if len(snippet) < 85:
                    published = parse_date(snippet)
                    if published:
                        date_from = '发布日期元素'
                        break
    raw_text = clean((soup.get_text(' ', strip=True)[:4000] if soup else main_text) or main_text)
    if not published:
        for match in list(DATE_LABEL.finditer(raw_text)) + list(GENERIC_DATE_LABEL.finditer(raw_text)):
            published = parse_date(match.group(1))
            if published:
                date_from = '正文发布日期字段'
                break
    # Publication and issue dates are different. Never silently substitute one for the other.
    if not published and listing_date:
        published, date_from = parse_date(listing_date), '来源列表日期'
    issued = next((parse_date(m.group(1)) for m in ISSUED_LABEL.finditer(raw_text) if parse_date(m.group(1))), '')
    effective = next((parse_date(m.group(1), allow_future=True) for m in EFFECTIVE_LABEL.finditer(raw_text) if parse_date(m.group(1), allow_future=True)), '')
    return {'published_at': published, 'date_evidence': date_from,
            'issued_at': issued, 'effective_at': effective}


def title_candidates(html: str, page_url: str, source_url: str) -> list[tuple[str, str, str]]:
    soup = BeautifulSoup(html, 'html.parser')
    out, seen = [], set()
    for link_node in soup.select('a[href]'):
        title = clean(link_node.get_text(' ', strip=True) or link_node.get('title', ''))
        if len(title) < 7 or len(title) > 190 or REJECT_TITLE.fullmatch(title):
            continue
        if not (POLICY.search(title) or WIND.search(title) or CROSS_SECTOR.search(title)):
            continue
        url = safe_http_url(urljoin(page_url, link_node.get('href', '')))
        if not url or url in seen or SKIP_PATH.search(url) or url == safe_http_url(page_url):
            continue
        if not same_public_site(source_url, url):
            continue
        seen.add(url)
        # Date usually shares a short <li>, <tr> or compact parent container with its link.
        listing_date = ''
        for parent in (link_node.parent, link_node.find_parent(['li', 'tr', 'article'])):
            if parent is None:
                continue
            sibling_text = clean(parent.get_text(' ', strip=True))
            if len(sibling_text) < 360:
                sibling_text = sibling_text.replace(title, '', 1)
                listing_date = parse_date(sibling_text)
                if listing_date:
                    break
        out.append((title, url, listing_date))
    # Preserve webpage order (usually newest first); do not elevate old wind documents above new ones.
    return out


def text_from_detail(html: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, 'html.parser')
    for node in soup(['script', 'style', 'nav', 'header', 'footer', 'aside', 'form']):
        node.decompose()
    heading = soup.find('h1')
    title = clean(heading.get_text(' ', strip=True)) if heading else ''
    chunks = []
    for selector in ('article', 'main', '#zoom', '.TRS_Editor', '#UCAP-CONTENT', '.article-content', '.article', '.content', '#content', '.pages_content', '.wenzhang'):
        for node in soup.select(selector)[:3]:
            text = clean(node.get_text(' ', strip=True))
            if 100 <= len(text) <= 24000:
                chunks.append(text)
    body = max(chunks, key=len) if chunks else clean((soup.body or soup).get_text(' ', strip=True))
    return title, body[:22000]


def match_dimensions(title: str, text: str, dims: list[dict]) -> list[str]:
    if len(dims) != 18:
        raise ValueError('Expected 18 regulatory dimensions')
    heading, body = title.lower(), text[:6000].lower()
    scores = []
    for dim, terms in zip(dims, DIMENSION_TERMS):
        hit = 4 * sum(term.lower() in heading for term in terms) + min(3, sum(term.lower() in body for term in terms))
        if hit >= 2:
            scores.append((hit, dim['name']))
    scores.sort(reverse=True)
    return [name for _, name in scores[:4]]


def detect_doc_type(title: str) -> str:
    if re.search(r'征求意见|草案|draft|public consultation', title, re.I):
        return '征求意见稿'
    if re.search(r'标准|技术规范|standard|grid code', title, re.I):
        return '技术标准或规范'
    if re.search(r'条例|法律|办法|规章|regulation|decree|law', title, re.I):
        return '法规规章候选'
    return '政策文件候选'


def review_candidate(title: str, url: str, source: dict, reason: str) -> dict:
    return {'title': title, 'url': url, 'source_id': source['id'], 'source_name': source['name'],
            'region': source['region'], 'reason': reason, 'checked_at': now_iso()}


def create_policy(title: str, url: str, html: str, source: dict, dims: list[dict],
                  *, is_pdf: bool = False, listing_date: str = '') -> tuple[dict | None, str]:
    title = clean(title)
    heading, body = ('', clean(html)) if is_pdf else text_from_detail(html)
    if heading and len(heading) < 180 and (WIND.search(heading) or POLICY.search(heading)):
        title = heading
    if len(body) < 130:
        return None, '正文不足或 PDF 为扫描图片，待人工核验'
    if not WIND.search(title + ' ' + body):
        return None, '未找到明确的风电业务适用证据'
    if not POLICY.search(title + ' ' + body):
        return None, '未识别为法规政策或技术标准文件'
    if not (WIND.search(title) and POLICY.search(title)) and not WIND.search(body):
        return None, '跨行业文件没有正文风电证据'
    dates = extract_dates('' if is_pdf else html, body, listing_date)
    if not dates['published_at']:
        return None, '无法核实发布日期：不应混入最近两周或每日增量结果'
    item = {
        'id': 'p_' + hashlib.sha256(url.encode('utf-8')).hexdigest()[:18],
        'title': title, 'url': url, 'source_name': source['name'], 'source_id': source['id'],
        'region': source['region'], 'country': source.get('country', ''), 'province': source.get('province', ''),
        'published_at': dates['published_at'], 'date_evidence': dates['date_evidence'],
        'issued_at': dates['issued_at'], 'effective_at': dates['effective_at'],
        'discovered_at': now_iso(), 'doc_type': detect_doc_type(title),
        'dimensions': match_dimensions(title, body, dims), 'source_excerpt': body[:550],
        'validity_status': '失效/废止（需核对原文）' if INACTIVE_RE.search(title + ' ' + body[:600]) else '效力待核验',
        'verification_status': '待核验（初筛候选，并非法律效力认定）',
        'impact_design': '待核验：根据具体条款判断是否影响机型参数、认证或并网设计。',
        'impact_business': '待核验：根据适用主体与生效条件判断是否影响销售、投标或供应链。',
        'next_step': '核对官方原文、附件、条款编号、适用范围和现行效力后再评估应对。',
        'article_evidence_available': True,
    }
    return item, ''


def within_days(published: str, days: int, today: date | None = None) -> bool:
    try:
        pub = date.fromisoformat(published)
        end = today or china_today()
        return end - timedelta(days=days - 1) <= pub <= end
    except (ValueError, TypeError):
        return False


def browser_page(url: str) -> tuple[str, str]:
    """Only public browser rendering; never solve CAPTCHA or evade explicit denial."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=['--no-sandbox'])
        try:
            page = browser.new_page(locale='zh-CN', viewport={'width': 1366, 'height': 850})
            response = page.goto(url, wait_until='domcontentloaded', timeout=18000)
            if response and response.status in (401, 403, 429):
                raise PermissionError(f'Public browser received HTTP {response.status}')
            page.wait_for_timeout(1000)
            return page.content(), safe_http_url(page.url)
        finally:
            browser.close()


def entry_points(source: dict) -> list[str]:
    points = [source.get('url', '')] + list(source.get('entry_urls') or [])
    result = []
    for url in points:
        safe = safe_http_url(url)
        if safe and safe not in result and same_public_site(source.get('url', ''), safe):
            result.append(safe)
    return result


def error_kind(exc: Exception) -> str:
    if isinstance(exc, requests.HTTPError):
        status = getattr(getattr(exc, 'response', None), 'status_code', None)
        if status in (401, 403, 429):
            return 'blocked'
        return 'error'
    if isinstance(exc, PermissionError):
        return 'blocked'
    if isinstance(exc, (requests.Timeout, TimeoutError)):
        return 'timeout'
    if isinstance(exc, requests.ConnectionError):
        return 'connection_error'
    return 'error'


def scrape_source(source: dict, dims: list[dict], days: int) -> tuple[list[dict], list[dict], dict]:
    start = time.monotonic()
    checked = now_iso()
    found, reviews = [], []
    status = {'id': source['id'], 'checked_at': checked, 'status': 'not_checked',
              'found_count': 0, 'candidate_links': 0, 'detail_scanned': 0,
              'out_of_window_count': 0, 'date_unknown_count': 0, 'detail_failures': 0,
              'browser_used': False, 'checked_days': days, 'attempted_urls': [], 'message': ''}
    urls = entry_points(source)
    if not urls:
        status.update(status='error', message='无合法的 HTTP(S) 官方来源网址')
        return found, reviews, status
    last_error = None
    html, final, is_pdf = '', '', False
    for url in urls:
        status['attempted_urls'].append(url)
        try:
            html, final, is_pdf = get_page(url)
            if not same_public_site(source['url'], final):
                raise ValueError('来源跳转至不相关域名')
            break
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            # An explicit HTTP block is not a signal to evade controls.
            if error_kind(exc) == 'blocked':
                break
    if not final:
        status.update(status=error_kind(last_error or RuntimeError('No response')),
                      message=(type(last_error).__name__ + ': ' + str(last_error))[:260] if last_error else '无法连接')
        return found, reviews, status
    if is_pdf:
        status.update(status='needs_adapter', message='来源是单篇 PDF，不是可持续抓取的政策目录')
        reviews.append(review_candidate(source['name'], final, source, '单篇 PDF 不可代替政策目录'))
        return found, reviews, status
    if source.get('crawl_mode') == 'single_document':
        status.update(status='needs_adapter', message='来源链接是单篇文章，需提供可更新的政策栏目')
        reviews.append(review_candidate(source['name'], final, source, '需替换成官方持续更新的栏目地址'))
        return found, reviews, status
    candidates = title_candidates(html, final, source['url'])
    # Search portals are no longer skipped outright: render public pages and inspect links.
    dynamic = source.get('crawl_mode') == 'special_adapter'
    if ENABLE_BROWSER and (dynamic or not candidates) and time.monotonic() - start < SOURCE_BUDGET - 20:
        try:
            rendered, browser_final = browser_page(final)
            if browser_final and same_public_site(source['url'], browser_final):
                extra = title_candidates(rendered, browser_final, source['url'])
                status['browser_used'] = True
                known = {c[1] for c in candidates}
                candidates += [c for c in extra if c[1] not in known]
        except Exception as exc:
            status['browser_message'] = (type(exc).__name__ + ': ' + str(exc))[:150]
    status['candidate_links'] = len(candidates)
    if not candidates:
        status.update(status='needs_adapter' if dynamic else 'ok_no_candidates',
                      message='需要可用的检索接口或栏目适配，当前未发现可核实的政策链接' if dynamic else '入口可读取，但未提取到政策文件链接')
        return found, reviews, status

    # Sites generally publish new items first. Inspect links in source order.
    for title, link, list_date in candidates:
        if status['detail_scanned'] >= MAX_DETAILS or time.monotonic() - start > SOURCE_BUDGET:
            break
        if list_date and not within_days(list_date, days):
            status['out_of_window_count'] += 1
            continue
        try:
            status['detail_scanned'] += 1
            time.sleep(0.12)  # Avoid rapid repeated hits to one institution.
            detail, detail_url, pdf = get_page(link)
            if not same_public_site(source['url'], detail_url):
                status['detail_failures'] += 1
                continue
            policy, reason = create_policy(title, detail_url, detail, source, dims, is_pdf=pdf,
                                           listing_date=list_date)
            if policy:
                if within_days(policy['published_at'], days):
                    found.append(policy)
                else:
                    status['out_of_window_count'] += 1
            else:
                if '发布日期' in reason:
                    status['date_unknown_count'] += 1
                if len(reviews) < 12:
                    reviews.append(review_candidate(title, detail_url, source, reason))
        except (requests.RequestException, ValueError) as exc:
            status['detail_failures'] += 1
            if len(reviews) < 12:
                reviews.append(review_candidate(title, link, source, '详情读取失败：' + str(exc)[:130]))
    status['found_count'] = len(found)
    if found:
        status.update(status='ok_with_results', message=f'确认发布日期落在最近{days}天的风电政策候选 {len(found)} 条')
    elif status['detail_scanned'] and status['detail_failures'] == status['detail_scanned']:
        status.update(status='detail_error', message='栏目可读，但详情页均未抓取成功')
    elif dynamic and status['date_unknown_count'] and not status['out_of_window_count']:
        status.update(status='needs_adapter', message='检索页有候选，但缺少可靠发布日期，需继续专用适配')
    else:
        status.update(status='ok_no_recent', message=f'栏目已检查；最近{days}天尚无可核实的风电法规候选')
    if status['detail_scanned'] >= MAX_DETAILS and len(candidates) > MAX_DETAILS:
        status['coverage_limited'] = True
        status['message'] += '；详情扫描达到每站上限，非全库覆盖'
    return found, reviews, status


def upgrade_existing(item: dict) -> dict:
    """Keep previous candidates, without pretending missing dates are verified."""
    record = dict(item)
    if not record.get('validity_status'):
        record['validity_status'] = ('失效/废止（需核对原文）'
                                     if INACTIVE_RE.search(record.get('title', '') + ' ' + record.get('source_excerpt', '')[:300])
                                     else '效力待核验')
    if not record.get('date_evidence'):
        record['date_evidence'] = '历史记录（待复核）' if record.get('published_at') else ''
    return record


def repair_legacy_date(item: dict) -> dict:
    """Revisit old records that were saved without any verified publication date."""
    item = dict(item)
    if parse_date(item.get('published_at', '')) or not safe_http_url(item.get('url', '')):
        return item
    try:
        content, resolved, pdf = get_page(item['url'])
        if not same_public_site(item['url'], resolved):
            return item
        _, main_text = ('', content) if pdf else text_from_detail(content)
        dates = extract_dates('' if pdf else content, main_text)
        if dates['published_at']:
            item.update(published_at=dates['published_at'], date_evidence='重新核查：' + dates['date_evidence'],
                        issued_at=dates['issued_at'], effective_at=dates['effective_at'])
    except (requests.RequestException, ValueError) as exc:
        LOG.debug('Could not revisit legacy date for %s: %s', item.get('url'), exc)
    return item


def save_csv(items: list[dict]):
    columns = ['发布日期', '发布日期依据', '国家/地区', '省份', '文件名称', '法规维度', '效力状态',
               '官方机构', '官方原文', '核验状态', '证据摘录']
    CSV_FILE.parent.mkdir(parents=True, exist_ok=True)
    with CSV_FILE.open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.writer(stream)
        writer.writerow(columns)
        for it in items:
            writer.writerow([it.get('published_at', ''), it.get('date_evidence', ''), it.get('region', ''),
                             it.get('province', ''), it.get('title', ''), '、'.join(it.get('dimensions', [])),
                             it.get('validity_status', ''), it.get('source_name', ''), it.get('url', ''),
                             it.get('verification_status', ''), it.get('source_excerpt', '')])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Scan public official policy listings')
    parser.add_argument('--days', type=int, default=int(os.getenv('POLICY_LOOKBACK_DAYS', '2')),
                        help='Calendar publication window (14 for manual backfill; 1 for daily)')
    parser.add_argument('--source-limit', type=int, default=int(os.getenv('POLICY_SOURCES_PER_RUN', '0')),
                        help='0 means all configured sources; positive number is a resumable batch')
    args = parser.parse_args(argv)
    if not (1 <= args.days <= 366):
        parser.error('--days must be within 1..366')
    all_sources, dims = read_json(SOURCES, []), read_json(DIMENSIONS, [])
    if not all_sources or len(dims) != 18:
        raise SystemExit('Policy sources configuration missing or dimensions != 18')
    DATA.mkdir(parents=True, exist_ok=True)
    state = read_json(STATE, {})
    # The first run after installing this patch is a 14-day backfill, even if
    # GitHub's daily schedule fires before a manual workflow dispatch.
    if not state.get('backfill_completed') and args.days in (1, 2):
        LOG.info('First updated run: automatically expanding daily request to a 14-day backfill')
        args.days = 14
    offset = int(state.get('next_index', 0)) % len(all_sources)
    limit = min(len(all_sources), args.source_limit) if args.source_limit > 0 else len(all_sources)
    subset = ([all_sources[(offset + i) % len(all_sources)] for i in range(limit)]
              if args.source_limit > 0 else all_sources)
    old_status = read_json(STATUSES, [])
    status_by_id = {x['id']: x for x in old_status if isinstance(x, dict) and x.get('id')}
    previous = read_json(POLICIES, [])
    item_by_url = {x['url']: upgrade_existing(x) for x in previous
                   if isinstance(x, dict) and x.get('url')}
    # The previous version saved several documents with unknown dates. Revisit
    # their official detail pages to repair the existing table, not only new rows.
    undated = [key for key, item in item_by_url.items() if not parse_date(item.get('published_at', ''))][:30]
    if undated:
        with ThreadPoolExecutor(max_workers=min(3, MAX_WORKERS)) as refresh_pool:
            for refreshed in refresh_pool.map(repair_legacy_date, (item_by_url[key] for key in undated)):
                item_by_url[refreshed['url']] = refreshed
        LOG.info('Rechecked publication dates on %s legacy candidates', len(undated))
    old_review = read_json(REVIEW, [])
    review_by_url = {x['url']: x for x in old_review if isinstance(x, dict) and x.get('url')}
    LOG.info('Scanning %s/%s sources for %s calendar days, workers=%s', len(subset), len(all_sources), args.days, MAX_WORKERS)
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        future_map = {pool.submit(scrape_source, source, dims, args.days): source for source in subset}
        for index, future in enumerate(as_completed(future_map), start=1):
            source = future_map[future]
            try:
                policies, reviews, status = future.result()
            except Exception as exc:
                policies, reviews = [], []
                status = {'id': source['id'], 'status': 'error', 'checked_at': now_iso(), 'found_count': 0,
                          'checked_days': args.days, 'message': ('Unexpected: ' + repr(exc))[:250]}
            status_by_id[source['id']] = status
            for policy in policies:
                old = item_by_url.get(policy['url'], {})
                # Preserve any separately verified downstream analysis instead of overwriting it.
                for key in ('importance_level', 'importance_score', 'impact_design', 'impact_business', 'next_step'):
                    if old.get(key) and '待核验' not in str(old[key]):
                        policy[key] = old[key]
                item_by_url[policy['url']] = policy
            for review in reviews:
                review_by_url[review['url']] = review
            LOG.info('%s/%s [%s] %s +%s', index, len(subset), status['status'], source['name'], len(policies))
    results = sorted(item_by_url.values(),
                     key=lambda item: (item.get('published_at') or '0000-00-00', item.get('discovered_at', '')),
                     reverse=True)[:MAX_ITEMS]
    review_results = sorted(review_by_url.values(), key=lambda item: item.get('checked_at', ''), reverse=True)[:MAX_REVIEW]
    statuses = [{'id': src['id'], 'status': 'not_checked', 'checked_at': '', 'found_count': 0,
                 'message': '尚未轮询'} | status_by_id.get(src['id'], {}) for src in all_sources]
    # Publish the actual source and dimension definitions to GitHub Pages.
    write_json(DATA / 'policy_sources.json', all_sources)
    write_json(DATA / 'regulatory_dimensions.json', dims)
    write_json(POLICIES, results)
    write_json(REVIEW, review_results)
    write_json(STATUSES, statuses)
    write_json(STATE, {'backfill_completed': bool(state.get('backfill_completed') or (args.days >= 14 and len(subset) == len(all_sources))),
                       'next_index': (offset + limit) % len(all_sources) if args.source_limit > 0 else 0,
                       'last_run': now_iso(), 'last_lookback_days': args.days,
                       'last_count': len(subset), 'configured_source_count': len(all_sources),
                       'new_recent_candidates': sum(1 for it in results if within_days(it.get('published_at', ''), args.days)),
                       'failed_sources': sum(s.get('status') in ('blocked', 'timeout', 'connection_error', 'error', 'detail_error', 'needs_adapter') for s in statuses)})
    save_csv(results)
    LOG.info('Finished. Stored=%s, review=%s, sources=%s', len(results), len(review_results), len(subset))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
