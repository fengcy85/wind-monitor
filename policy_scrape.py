"""Conservative policy-source discovery for the V5 wind-monitor site.

The program finds candidates from official public listings; it does not assert
legal force or auto-approve an inferred obligation. Search-only government
portals require a dedicated adapter and are explicitly not marked as crawled.
"""
from __future__ import annotations

import csv
import hashlib
import ipaddress
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit, parse_qsl, urlencode

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "site" / "data"
DATA.mkdir(parents=True, exist_ok=True)
SOURCES = ROOT / "policy_sources.json"
DIMENSIONS = ROOT / "regulatory_dimensions.json"
POLICIES = DATA / "policies.json"
REVIEW = DATA / "policy_candidates_review.json"
STATUSES = DATA / "policy_source_status.json"
STATE = DATA / "policy_crawl_state.json"
CSV_FILE = DATA / "policies.csv"

MAX_PER_RUN = max(1, int(os.getenv("POLICY_SOURCES_PER_RUN", "24")))
MAX_DETAILS = min(8, max(1, int(os.getenv("POLICY_DETAILS_PER_SOURCE", "3"))))
TIMEOUT = min(25.0, max(2.0, float(os.getenv("POLICY_REQUEST_TIMEOUT", "9"))))
MAX_ITEMS = 700
MAX_REVIEW = 300
LOG = logging.getLogger("wind-policy-v5")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; WindPolicyMonitor/5.1; public policy research)",
    "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.8,*/*;q=0.3",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
})

# A dimension can be inferred for triage, but never as proof of legal applicability.
DIMENSION_TERMS = [
    ["市场准入", "外商投资", "外资准入", "进口许可", "投资准入", "负面清单", "foreign ownership", "market access"],
    ["本地化", "原产地", "本地生产", "国产化", "国内制造", "local content", "rules of origin", "供应链本土化"],
    ["标准", "认证", "检验检测", "型式认证", "国家标准", "行业标准", "iec 61400", "产品技术规范"],
    ["电网接入", "电网建设", "电力接入", "接入系统", "输电", "配套送出", "输变电", "grid connection"],
    ["并网", "低电压穿越", "高电压穿越", "频率响应", "无功", "电能质量", "lvrt", "hvrt", "grid code"],
    ["电价", "绿证", "可再生能源开发", "新能源发展", "风电开发", "新能源消纳", "购电协议", "ppa", "市场化交易"],
    ["招投标", "招标", "政府采购", "投标资格", "采购办法", "资格预审", "tender", "bidding"],
    ["关税", "税收", "反倾销", "反补贴", "进口税", "出口税", "贸易救济", "增值税", "关税配额"],
    ["超限运输", "大件运输", "道路运输", "车辆许可", "桥梁承载", "港口吊装", "运输许可"],
    ["环评", "环境影响", "生态保护", "土地使用", "用地", "噪声", "鸟类", "海洋生态", "环境保护"],
    ["航空", "民航", "净空", "限高", "雷达", "军事", "国防", "机场保护"],
    ["台风", "沙尘", "雷电", "盐雾", "耐低温", "抗震", "极端风速", "极端温度"],
    ["网络安全", "数据安全", "数据跨境", "关键基础设施", "远程访问", "scada", "数据出境"],
    ["安全生产", "职业安全", "消防", "特种作业", "劳动保护", "起重吊装", "电气安全"],
    ["海上风电", "海洋工程", "海域使用", "海事", "海底电缆", "offshore wind"],
    ["绿色金融", "项目融资", "贷款", "融资担保", "出口信贷", "银行授信", "外汇"],
    ["运维", "退役", "质保", "维修", "可回收", "回收利用", "decommission", "operation and maintenance"],
    ["碳足迹", "碳排放", "碳核算", "供应链尽职", "esg", "绿色采购", "材料溯源", "绿色制造"],
]
WIND = re.compile(r"风力发电|风电|风力机|风电机组|风力发电机组|wind\s+(?:power|energy|farm|turbine)|windfarm|onshore\s+wind|offshore\s+wind", re.I)
POLICY = re.compile(r"条例|法规|法律|办法|规定|规则|通知|意见|标准|规范|政策|技术要求|实施方案|指导目录|准入|认证|招标|并网|规划|修订|废止|批复|采购|电价|tariff|regulat|standard|decree|law|policy|resolution|rule|grid code|tender", re.I)
CROSS_SECTOR = re.compile(r"电力|新能源|可再生能源|能源|并网|输电|电网|标准|装备|制造|海洋|数据安全|运输|采购|认证|electricity|renewable|grid|energy", re.I)
REJECT_TITLE = re.compile(r"^(首页|更多|更多信息|政策法规|政策文件|信息公开|政策解读|通知公告|联系我们|上一页|下一页|政府信息公开|政务公开|点击查看)$", re.I)
SKIP_PATH = re.compile(r"\.(?:jpg|jpeg|png|gif|svg|js|css|zip|xlsx?|docx?|mp4|mp3|ico)(?:\?|$)", re.I)
PDF_PATH = re.compile(r"\.pdf(?:\?|$)", re.I)
DATE_RE = re.compile(r"(20[1-3]\d)[-年/\.](0[1-9]|1[0-2]|[1-9])[-月/\.](0[1-9]|[12]\d|3[01]|[1-9])(?=日|\D|$)(?:日)?")


def read_json(path: Path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback


def write_json(path: Path, payload):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean(s: object) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def safe_http_url(value: str) -> str:
    try:
        p = urlsplit(value.strip())
        if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
            return ""
        host = p.hostname.lower()
        if host in ("localhost", "127.0.0.1", "0.0.0.0", "::1") or host.endswith((".local",".internal")):
            return ""
        try:
            if not ipaddress.ip_address(host).is_global:
                return ""
        except ValueError:
            pass
        q = [(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True) if not k.lower().startswith("utm_") and k.lower() not in ("fbclid","gclid")]
        return urlunsplit((p.scheme,p.netloc,p.path or "/",urlencode(q),""))
    except ValueError:
        return ""


def same_public_site(base: str, child: str) -> bool:
    a=urlsplit(base).hostname or ""
    b=urlsplit(child).hostname or ""
    a=a.removeprefix('www.').lower();b=b.removeprefix('www.').lower()
    if a==b or a.endswith('.'+b) or b.endswith('.'+a): return True
    # China State Council file database may link to its own www.gov.cn article portal.
    if a.endswith('www.gov.cn') and b.endswith('www.gov.cn'):return True
    return False


def get_page(url: str) -> tuple[str, str, bool]:
    resp=SESSION.get(url, timeout=(min(5,TIMEOUT),TIMEOUT),allow_redirects=True)
    resp.raise_for_status()
    final=safe_http_url(resp.url)
    if not final: raise ValueError('response redirected to non-http URL')
    ctype=resp.headers.get('Content-Type','').lower()
    if 'pdf' in ctype or PDF_PATH.search(final):return '',final,True
    if 'html' not in ctype and 'text/' not in ctype and not resp.content[:50].lstrip().startswith(b'<'):
        raise ValueError(f'unsupported content type: {ctype[:50]}')
    if len(resp.content)>6_000_000:raise ValueError('HTML response too large')
    resp.encoding=resp.apparent_encoding if resp.encoding is None or resp.encoding.lower() in ('iso-8859-1','ascii') else resp.encoding
    return resp.text,final,False


def title_candidates(html: str, page_url: str, source_url: str) -> list[tuple[str,str]]:
    soup=BeautifulSoup(html,'html.parser')
    out=[];seen=set()
    for a in soup.select('a[href]'):
        title=clean(a.get_text(' ',strip=True))
        if len(title)<7 or len(title)>190 or REJECT_TITLE.fullmatch(title):continue
        if not (POLICY.search(title) or WIND.search(title) or CROSS_SECTOR.search(title)):
            continue
        link=safe_http_url(urljoin(page_url,a.get('href','')))
        if not link or link in seen or SKIP_PATH.search(link):continue
        if not same_public_site(source_url,link):continue
        if link==safe_http_url(page_url):continue
        seen.add(link);out.append((title,link))
    def rank(item):
        title=item[0]
        return (3*bool(WIND.search(title))+2*bool(POLICY.search(title))+bool(CROSS_SECTOR.search(title)),len(title))
    return sorted(out,key=rank,reverse=True)


def text_from_detail(html: str) -> tuple[str,str]:
    soup=BeautifulSoup(html,'html.parser')
    for t in soup(['script','style','nav','header','footer','aside','form']):t.decompose()
    heading=soup.find('h1')
    page_title=clean(heading.get_text(' ',strip=True)) if heading else ''
    choices=[]
    for selector in ('article','main','#zoom','.TRS_Editor','#UCAP-CONTENT','.article-content','.article','.content','#content','.pages_content','.wenzhang'):
        for elem in soup.select(selector)[:4]:
            text=clean(elem.get_text(' ',strip=True))
            if len(text)>110:choices.append((len(text),text))
    if choices:
        # A small article container is preferable to a huge wrapper with navigation.
        main=sorted(choices,key=lambda x:x[0])[0][1]
        medium=[t for size,t in choices if 110<=size<=12000]
        if medium:main=max(medium,key=len)
    else:
        main=clean((soup.body or soup).get_text(' ',strip=True))
    return page_title,main[:20000]


def derive_date(html: str, body: str) -> str:
    soup=BeautifulSoup(html,'html.parser')
    vals=[]
    for attrs in ({'property':'article:published_time'},{'name':'publishdate'},{'name':'PubDate'},{'name':'date'}):
        tag=soup.find('meta',attrs=attrs)
        if tag:vals.append(tag.get('content',''))
    vals.extend(t.get('datetime','') for t in soup.find_all('time',limit=4))
    for el in soup.select('.date,.pubtime,.publish_time,.pubDate,.time')[:6]:vals.append(el.get_text(' ',strip=True))
    vals.append(body[:250])
    for v in vals:
        m=DATE_RE.search(str(v))
        if not m:continue
        try:
            d=datetime(int(m.group(1)),int(m.group(2)),int(m.group(3)))
            if d.year>datetime.now(timezone.utc).year+1:continue
            return d.date().isoformat()
        except ValueError:continue
    return ''


def match_dimensions(title: str, text: str, dims: list[dict]) -> list[str]:
    assert len(DIMENSION_TERMS)==len(dims)
    title=clean(title).lower();body=clean(text[:5500]).lower()
    scores=[]
    for dimension,terms in zip(dims,DIMENSION_TERMS):
        title_hits=sum(t.lower() in title for t in terms)
        body_hits=sum(t.lower() in body for t in terms)
        score=4*title_hits+min(body_hits,3)
        if score>=2:scores.append((score,dimension['name']))
    scores.sort(reverse=True)
    return [name for _,name in scores[:4]]


def detect_doc_type(title: str) -> str:
    if re.search(r'征求意见|草案|draft|public consultation',title,re.I):return '征求意见稿'
    if re.search(r'标准|技术规范|standard|grid code',title,re.I):return '技术标准或规范'
    if re.search(r'条例|法律|办法|规章|regulation|decree|law',title,re.I):return '法规规章候选'
    return '政策文件候选'


def review_candidate(title: str,url: str,source: dict,reason: str) -> dict:
    return {'title':title,'url':url,'source_id':source['id'],'source_name':source['name'],'region':source['region'],
            'reason':reason,'checked_at':now_iso()}


def create_policy(title: str,url: str,html: str, source: dict,dims: list[dict],is_pdf: bool=False) -> tuple[dict|None,str]:
    title=clean(title)
    head,body=text_from_detail(html) if html else ('','')
    if head and (WIND.search(head) or POLICY.search(head)) and len(head)<170:title=head
    if is_pdf:
        # Reading a PDF URL is not evidence of its contents. Keep it for human review instead.
        return None,'PDF原文尚未解析，待人工核对全文'
    if len(body)<130:return None,'无法取得足够的正式正文，待核验'
    if not WIND.search(title+' '+body):return None,'尚未证实该文件与风电业务的适用关系'
    if not POLICY.search(title+' '+body):return None,'未识别为法规政策或技术标准文件'
    if not (WIND.search(title) and POLICY.search(title)):
        # Generic policies require a specific wind citation in the article body.
        if not WIND.search(body):return None,'跨行业法规尚无风电适用性证据'
        if not (POLICY.search(title) or POLICY.search(body[:1400])):return None,'缺少明确政策规范信号'
    # Source text is factual; inferred dimensions/actions are explicitly triage-only.
    names=match_dimensions(title,body,dims)
    pub=derive_date(html,body)
    uid='p_'+hashlib.sha256(url.encode('utf-8')).hexdigest()[:18]
    item={
      'id':uid,'title':title,'url':url,'source_name':source['name'],'source_id':source['id'],
      'region':source['region'],'country':source['country'],'province':source.get('province',''),
      'published_at':pub,'discovered_at':now_iso(),'doc_type':detect_doc_type(title),
      'dimensions':names,'source_excerpt':body[:340],
      'verification_status':'待核验（仅识别候选，非法律效力认定）',
      'impact_design':'待核验：根据具体条款判断是否影响机型参数、认证或并网设计。',
      'impact_business':'待核验：根据适用主体与生效条件判断是否影响销售、投标或供应链。',
      'next_step':'核对官方原文、附件、条款编号、适用范围、生效日期和修订状态后再评估应对。',
      'article_evidence_available':True,
    }
    return item,''


def scrape_source(source: dict,dims: list[dict]) -> tuple[list[dict],list[dict],dict]:
    checked=now_iso()
    if source.get('crawl_mode')=='special_adapter':
        return [],[],{'id':source['id'],'status':'needs_adapter','checked_at':checked,'found_count':0,
                     'message':'查询型/动态页面需要专用适配，不能按普通静态列表判定抓取成功'}
    url=safe_http_url(source['url'])
    if not url:
        return [],[],{'id':source['id'],'status':'error','checked_at':checked,'found_count':0,'message':'无有效 HTTP/HTTPS URL'}
    try:
        html,final,is_pdf=get_page(url)
        if not same_public_site(url,final):raise ValueError('source URL redirected outside the source website')
        if is_pdf:
            return [],[review_candidate(source['name'],url,source,'来源入口为 PDF，待人工核验')],{
                'id':source['id'],'status':'needs_adapter','checked_at':checked,'found_count':0,'message':'PDF 入口需要全文解析'}
        candidates=title_candidates(html,final,url) if source['crawl_mode']=='html_listing' else [(source['name'],final)]
        found=[];reviews=[]
        for title,link in candidates[:MAX_DETAILS]:
            try:
                page,redirect,pdf=get_page(link) if link!=final else (html,final,False)
                if not same_public_site(url,redirect):continue
                policy,reason=create_policy(title,redirect,page,source,dims,is_pdf=pdf)
                if policy:found.append(policy)
                elif len(reviews)<10:reviews.append(review_candidate(title,redirect,source,reason))
            except (requests.RequestException,ValueError) as exc:
                reviews.append(review_candidate(title,link,source,'详情页面读取失败：'+str(exc)[:100]))
        state='ok_with_results' if found else ('ok_no_relevant' if candidates else 'ok_no_candidates')
        msg=('发现风电法规候选，法律效力待核验' if found else '入口可读取，但本轮未确认风电法规文件')
        if source.get('crawl_mode')=='single_document' and not found:
            msg='这是单篇文件入口，并非可持续更新的目录；需替换为栏目页'
        return found,reviews,{'id':source['id'],'status':state,'checked_at':checked,'found_count':len(found),
                              'candidate_links':len(candidates),'message':msg}
    except (requests.RequestException,ValueError) as exc:
        return [],[],{'id':source['id'],'status':'error','checked_at':checked,'found_count':0,
                     'message':(type(exc).__name__+': '+str(exc))[:240]}


def save_csv(items:list[dict]):
    names=['发布日期','国家/地区','省份','文件名称','文件类型','法规维度','机构','官方来源网址','核验状态','正文摘录']
    with CSV_FILE.open('w',newline='',encoding='utf-8-sig') as out:
        w=csv.writer(out);w.writerow(names)
        for x in items:
            w.writerow([x.get('published_at'),x.get('region'),x.get('province'),x.get('title'),x.get('doc_type'),
                        '、'.join(x.get('dimensions',[])),x.get('source_name'),x.get('url'),
                        x.get('verification_status'),x.get('source_excerpt')])


def main():
    all_sources=read_json(SOURCES,[]);dims=read_json(DIMENSIONS,[])
    if not all_sources or len(dims)!=18:raise SystemExit('来源或法规维度配置缺失')
    state=read_json(STATE,{'next_index':0})
    offset=int(state.get('next_index',0))%len(all_sources)
    limit=min(MAX_PER_RUN,len(all_sources))
    subset=[all_sources[(offset+i)%len(all_sources)] for i in range(limit)]
    old_status=read_json(STATUSES,[]);status_by_id={s['id']:s for s in old_status if isinstance(s,dict) and s.get('id')}
    items=read_json(POLICIES,[]);item_by_url={p.get('url'):p for p in items if isinstance(p,dict) and p.get('url')}
    old_review=read_json(REVIEW,[]);review_by_url={r.get('url'):r for r in old_review if isinstance(r,dict) and r.get('url')}
    for i,source in enumerate(subset,1):
        LOG.info('%s/%s %s',i,len(subset),source['name'])
        policies,review,status=scrape_source(source,dims)
        status_by_id[source['id']]=status
        for p in policies:item_by_url[p['url']]=p
        for r in review:review_by_url[r['url']]=r
        LOG.info('status=%s found=%s',status['status'],len(policies))
    result=list(item_by_url.values());result.sort(key=lambda p:(p.get('published_at') or '0000-00-00',p.get('discovered_at') or ''),reverse=True)
    review_result=list(review_by_url.values());review_result.sort(key=lambda p:p.get('checked_at',''),reverse=True)
    statuses=[]
    for src in all_sources:
        statuses.append({'id':src['id'],'status':'not_checked','checked_at':'','found_count':0,'message':'尚未轮询'}|
                        status_by_id.get(src['id'],{}))
    write_json(POLICIES,result[:MAX_ITEMS]);write_json(REVIEW,review_result[:MAX_REVIEW]);write_json(STATUSES,statuses)
    write_json(STATE,{'next_index':(offset+limit)%len(all_sources),'last_run':now_iso(),'last_count':limit,
                      'configured_source_count':len(all_sources),'wind_related_candidates':len(result)})
    save_csv(result[:MAX_ITEMS])
    LOG.info('total_policies=%s next_index=%s',len(result),(offset+limit)%len(all_sources))

if __name__=='__main__':main()
