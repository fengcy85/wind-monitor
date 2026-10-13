"""Respectful HTML/RSS/PDF crawler with per-source fallbacks and explicit failures.

Not an anti-bot bypass tool: honours robots.txt, access limits and CAPTCHA/403.
Foreign site modes are configured individually in policy_sources.json.
"""
from __future__ import annotations

import io
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit, urlencode, parse_qsl, urlunsplit
from urllib.robotparser import RobotFileParser
from xml.etree import ElementTree

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
try:
    import feedparser
except ImportError:
    feedparser=None
from pypdf import PdfReader

from policy_logic import WIND, REGULATORY, HINTS, matches, canonical_url, normalize_text, analyze_document

LOGGER=logging.getLogger('policy.crawler')
UA=os.environ.get('POLICY_USER_AGENT','WindPolicyMonitoringResearch/1.0 (public-source research; obeys robots.txt)')
TIMEOUT=float(os.environ.get('POLICY_REQUEST_TIMEOUT','10'))
PER_SOURCE=float(os.environ.get('POLICY_SOURCE_BUDGET_SECONDS','36'))
PDF_CAP=int(os.environ.get('POLICY_PDF_MAX_MB','12'))*1024*1024
HOST_SPACING=float(os.environ.get('POLICY_HOST_PAUSE_SECONDS','0.7'))
THREAD_CTX=threading.local()
ROBOTS={}
ROBOTS_LOCK=threading.RLock()
HOST_REQUEST_AT={}
SITEMAPS={}
PACE_LOCK=threading.RLock()


class FetchProblem(Exception):
    def __init__(self,code:str,message:str=''):
        self.code=code;super().__init__(message or code)


def session() -> requests.Session:
    if not hasattr(THREAD_CTX,'session'):
        s=requests.Session()
        retry=Retry(total=2,connect=2,read=1,backoff_factor=1.0,
            status_forcelist=[500,502,503,504],allowed_methods=['GET'],
            respect_retry_after_header=False,raise_on_status=False)
        adapter=HTTPAdapter(max_retries=retry,pool_connections=4,pool_maxsize=4)
        s.mount('https://',adapter);s.mount('http://',adapter)
        s.headers.update({'User-Agent':UA,'Accept':'text/html,application/xhtml+xml,application/xml,text/xml,application/pdf;q=0.9,*/*;q=0.6','Accept-Language':'zh-CN,zh;q=0.9,en;q=0.7'})
        THREAD_CTX.session=s
    return THREAD_CTX.session


def site_host(url:str) -> str:
    return (urlsplit(url).hostname or '').lower()


def allowed_link(url:str,source:dict) -> bool:
    """Follow same host & subdomains only, never private networks or offsite links."""
    try:
        p=urlsplit(url)
        if p.scheme not in ('https','http') or not p.hostname or p.username or p.password:return False
        host=p.hostname.lower();base=site_host(source['url'])
        if host not in (base,base.removeprefix('www.')) and not host.endswith('.'+base.removeprefix('www.')):
            return False
        if host in ('localhost','127.0.0.1','0.0.0.0','::1') or host.startswith(('10.','192.168.','172.16.')):return False
        return True
    except ValueError:return False


def paced(host:str) -> None:
    with PACE_LOCK:
        now=time.monotonic();last=HOST_REQUEST_AT.get(host,0.0)
        wait=max(0,HOST_SPACING-(now-last))
        if wait:time.sleep(wait)
        HOST_REQUEST_AT[host]=time.monotonic()


def robots_allowed(url:str) -> bool:
    host=site_host(url)
    with ROBOTS_LOCK:
        if host not in ROBOTS:
            robots_url=urlsplit(url).scheme+'://'+urlsplit(url).netloc+'/robots.txt'
            try:
                paced(host)
                r=session().get(robots_url,timeout=(3,6),allow_redirects=True)
                if r.status_code in (401,403):
                    ROBOTS[host]='deny'
                elif r.status_code==404:
                    ROBOTS[host]='missing'
                elif r.ok and r.text.strip():
                    parser=RobotFileParser();parser.parse(r.text.splitlines())
                    SITEMAPS[host]=[line.split(':',1)[1].strip() for line in r.text.splitlines() if line.lower().startswith('sitemap:')][:2]
                    ROBOTS[host]=parser
                else:
                    ROBOTS[host]='error'
            except (requests.RequestException,ValueError):
                ROBOTS[host]='error'
        rule=ROBOTS[host]
    if rule=='missing':return True
    if rule=='deny':raise FetchProblem('robots_denied','robots.txt 明确不允许本轮访问')
    if rule=='error':raise FetchProblem('robots_unavailable','robots.txt 无法核验，本轮谨慎跳过')
    if not rule.can_fetch(UA,url):raise FetchProblem('robots_denied','robots.txt 排除该路径')
    return True


def fetch(url:str,source:dict,max_bytes:int=3_000_000) -> tuple[bytes,str,str]:
    if not allowed_link(url,source):raise FetchProblem('offsite_link','跨站链接须另行确认')
    robots_allowed(url);paced(site_host(url))
    try:
        with session().get(url,timeout=(4,TIMEOUT),allow_redirects=True,stream=True) as r:
            if r.status_code in (401,403):raise FetchProblem('access_restricted',f'HTTP {r.status_code}（不绕过访问限制）')
            if r.status_code==429:raise FetchProblem('rate_limited','HTTP 429，已结束该站点本轮尝试')
            if not r.ok:raise FetchProblem('http_error',f'HTTP {r.status_code}')
            if not allowed_link(r.url,source):raise FetchProblem('offsite_redirect','跳转至站外，需专用适配')
            typ=(r.headers.get('content-type') or '').lower()
            if 'image/' in typ or 'application/octet-stream' in typ and not r.url.lower().endswith('.pdf'):
                raise FetchProblem('unsupported_file','非可解析 HTML/RSS/PDF')
            total=0;parts=[]
            for block in r.iter_content(chunk_size=65536):
                if not block:continue
                total+=len(block)
                if total>max_bytes:raise FetchProblem('too_large','页面或 PDF 超过大小上限')
                parts.append(block)
            return b''.join(parts),typ,r.url
    except FetchProblem:raise
    except requests.Timeout as e:raise FetchProblem('timeout',str(e)[:120]) from e
    except requests.RequestException as e:raise FetchProblem('network_error',str(e)[:180]) from e


def soup_html(raw:bytes) -> BeautifulSoup:
    return BeautifulSoup(raw,'html.parser')


def meta_content(soup:BeautifulSoup,keys:list[str]) -> str|None:
    for key in keys:
        found=soup.find('meta',attrs={'property':key}) or soup.find('meta',attrs={'name':key})
        if found and found.get('content'):return normalize_text(found['content'])
    return None


def parse_date(s:str|None) -> str|None:
    if not s:return None
    s=normalize_text(s)
    m=re.search(r'(?<!\d)(20\d{2}|19\d{2})[-/.年]\s*(\d{1,2})[-/.月]\s*(\d{1,2})',s)
    if m:
        try:return f'{int(m[1]):04d}-{int(m[2]):02d}-{int(m[3]):02d}' if datetime(int(m[1]),int(m[2]),int(m[3])) else None
        except ValueError:return None
    return None


def page_title(soup:BeautifulSoup) -> str:
    for selector in ['h1','meta[property="og:title"]','meta[name="citation_title"]','title']:
        el=soup.select_one(selector)
        if el:
            title=el.get('content') or el.get_text(' ',strip=True)
            if len(title)>5:
                return normalize_text(title.split(' | ')[0].split(' - ')[0])[:240]
    return ''


def article_text(soup:BeautifulSoup) -> tuple[str,list[str]]:
    """Strip menus, nav/footer and select the most substantive DOM article container."""
    soup=soup_html(str(soup).encode('utf-8'))  # isolated copy; no mutation of link discovery DOM
    for el in soup.select('script,style,noscript,nav,header,footer,aside,form,menu,button,select,.breadcrumb,.pagination,.related,.sidebar'):
        el.decompose()
    containers=[]
    for selector in ['article','main','.TRS_Editor','#TRS_Editor','.article-content','.article_content','#zoom','#zoomcon','#content','.content-detail','.document-content','.news-detail','.detail-content']:
        for el in soup.select(selector):
            size=len(el.get_text(' ',strip=True))
            if size>=90:containers.append((size,el))
    if containers:
        # Max text is not necessarily the best but containers have already been filtered.
        el=max(containers,key=lambda v:v[0])[1]
    else:
        el=soup.body or soup
    parts=[]
    for node in el.select('p,li,td,h2,h3,h4'):
        text=normalize_text(node.get_text(' ',strip=True))
        if len(text)>=18 and text not in parts:parts.append(text)
    if not parts:
        text=el.get_text('\n',strip=True)
        parts=[normalize_text(part) for part in text.split('\n') if len(normalize_text(part))>=28]
    return '\n\n'.join(parts)[:45000],parts[:160]


def pdf_text(raw:bytes) -> tuple[str,list[str]]:
    try:
        reader=PdfReader(io.BytesIO(raw))
        pages=[]
        for i,page in enumerate(reader.pages[:25]):
            val=normalize_text(page.extract_text() or '')
            if val:pages.append(val[:3500])
        alltext='\n\n'.join(pages)
        if len(alltext)<80:raise FetchProblem('pdf_image_only','PDF 无可提取文字，可能需人工 OCR')
        return alltext,pages
    except FetchProblem:raise
    except Exception as e:raise FetchProblem('pdf_unreadable',str(e)[:170]) from e


def links_from_html(html:bytes,base:str,source:dict) -> tuple[list[tuple[str,str]],list[str]]:
    soup=soup_html(html)
    scored={}; feeds=[]
    for link in soup.select('link[rel="alternate"]'):
        typ=str(link.get('type') or '').lower();href=link.get('href')
        if href and any(f in typ for f in ('rss','atom','xml')):
            full=canonical_url(urljoin(base,href))
            if allowed_link(full,source):feeds.append(full)
    for a in soup.find_all('a',href=True):
        href=a['href'];url=canonical_url(urljoin(base,href));text=normalize_text(a.get_text(' ',strip=True))
        if not url or not allowed_link(url,source) or url==canonical_url(base):continue
        lower=url.lower()
        if any(x in lower for x in ('mailto:','javascript:','/login','/register','/signin','/account','/share','/tag/','/search?','/category/')):continue
        if len(text)<7: text=normalize_text(a.get('title',''))
        if len(text)<7 and not lower.endswith('.pdf'):continue
        score=0
        if matches(text,REGULATORY):score+=9
        if matches(text,WIND):score+=11
        if any(matches(text,list(kw)) for kw in HINTS.values()):score+=7
        if matches(url.replace('-',' ').replace('_',' '),WIND):score+=4
        if re.search(r'/(?:\d{4}/\d{1,2}|policy|laws?|regulat|legal|notice|standard|doc|article|zcfg|zcwj|fg|bszn|flfg|detail|read|pdf)',lower):score+=4
        if lower.endswith('.pdf'):score+=2
        if text and re.search(r'(法规|条例|办法|通知|公告|决定|令|规定|标准|征求意见)',text):score+=4
        if matches(text,['新闻','news','活动','招聘','招人','interview','podcast','press release']):score-=12
        if score<7:continue
        if url in scored and scored[url][0]>=score:continue
        scored[url]=(score,text[:220])
    arr=sorted(((u,s,t) for u,(s,t) in scored.items()),key=lambda row:-row[1])
    # Diverse discovery: retain some general-law titles for body screening.
    return [(u,t) for u,_,t in arr[:source.get('max_links',10)*3]],list(dict.fromkeys(feeds))[:3]


def links_from_feed(raw:bytes,base:str,source:dict) -> list[tuple[str,str]]:
    arr=[]
    if feedparser:
        rss=feedparser.parse(raw)
        for entry in rss.entries[:28]:
            url=canonical_url(urljoin(base,entry.get('link','')))
            if url and allowed_link(url,source):arr.append((url,normalize_text(entry.get('title',''))))
    else:
        try:
            root=ElementTree.fromstring(raw)
            for el in root.iter():
                if el.tag.split('}')[-1] not in ('item','entry'):continue
                title='';link=''
                for child in el:
                    if child.tag.split('}')[-1]=='title':title=(child.text or '').strip()
                    if child.tag.split('}')[-1]=='link':link=child.get('href') or (child.text or '')
                url=canonical_url(urljoin(base,link))
                if url and allowed_link(url,source):arr.append((url,title))
        except ElementTree.ParseError:pass
    return arr[:28]


def get_form_search_links(html:bytes, base:str, source:dict) -> list[str]:
    """Follow only public GET search forms explicitly advertised by the site."""
    if source.get('strategy')!='search_portal':return []
    soup=soup_html(html);res=[]
    for form in soup.select('form')[:6]:
        if form.get('method','get').lower() != 'get':continue
        action=canonical_url(urljoin(base,form.get('action') or base))
        if not allowed_link(action,source):continue
        fields={}
        query_key=None
        for element in form.select('input[name]'):
            name=element.get('name','');typ=(element.get('type') or 'text').lower()
            if re.search(r'(search|keyword|query|searchtitle|term|^q$|^text$)',name,re.I) and typ not in ('hidden','submit') and not query_key:
                query_key=name
            elif typ=='hidden' and len(element.get('value','')) < 150:
                fields[name]=element.get('value','')
        if not query_key:continue
        # Multiple regional languages may require dedicated adapters; 1 conservative query only.
        fields[query_key]='wind'
        parsed=urlsplit(action)
        existing=dict(parse_qsl(parsed.query,keep_blank_values=True))
        existing.update(fields)
        url=urlunsplit((parsed.scheme,parsed.netloc,parsed.path,urlencode(existing),'')).replace('+','%20')
        if allowed_link(url,source):res.append(url)
    return res[:1]


def sitemap_links(source:dict) -> list[tuple[str,str]]:
    """Fallback to XML sitemap advertised in robots.txt; never guess an API endpoint."""
    links=[]
    for url in SITEMAPS.get(site_host(source['url']),[])[:1]:
        if not allowed_link(url,source):continue
        try:
            raw,typ,real=fetch(url,source,max_bytes=1_300_000)
            root=ElementTree.fromstring(raw)
            urls=[]
            for el in root.iter():
                if el.tag.split('}')[-1]=='loc' and el.text:
                    candidate=canonical_url(el.text.strip())
                    if allowed_link(candidate,source):urls.append(candidate)
            for candidate in urls[:400]:
                if any(tok in candidate.lower() for tok in ('wind','policy','regulat','standard','legal','zcfg','zcwj','articles','documents','2026','2025')):
                    links.append((candidate,'官方 sitemap 发现的候选页面'))
                    if len(links)>=12:break
        except (FetchProblem,ElementTree.ParseError):continue
    return links

def browser_render(url:str,source:dict) -> bytes:
    """Optional JS rendering of public site. No CAPTCHA bypass, proxies or stealth."""
    if os.getenv('POLICY_ENABLE_BROWSER','0')!='1':
        raise FetchProblem('browser_disabled','浏览器回退未启用')
    if not allowed_link(url,source):raise FetchProblem('offsite_link')
    robots_allowed(url)
    try:
        from playwright.sync_api import sync_playwright,TimeoutError as PWTimeout
    except ImportError as e:raise FetchProblem('browser_missing','未安装 playwright') from e
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True,args=['--no-sandbox'])
            context=browser.new_context(user_agent=UA,locale='en-US')
            page=context.new_page()
            page.goto(url,wait_until='domcontentloaded',timeout=16000)
            page.wait_for_timeout(1600)
            if re.search(r'captcha|human verification|access denied|请完成验证',page.title(),re.I):
                raise FetchProblem('access_restricted','检测到人工验证，未尝试绕过')
            body=page.content().encode('utf-8')
            browser.close()
            return body
    except FetchProblem:raise
    except Exception as e:raise FetchProblem('browser_failed',str(e)[:160]) from e


def scrape_source(source:dict,now:str|None=None) -> tuple[list[dict],list[dict],dict]:
    """Fetch an actual listing and specific article pages, verify wind evidence, report errors."""
    start=time.monotonic();now=now or datetime.now(timezone.utc).isoformat(timespec='seconds')
    status={'id':source['id'],'name':source['name'],'region':source['region'],'country':source['country'],'url':source['url'],
            'strategy':source['strategy'],'checked_at':now,'status':'not_run','status_message':'',
            'articles_checked':0,'links_discovered':0,'accepted':0,'pending':0,'http_success':False,
            'failure_details':[],'adapter_used':[]}
    pool=[];pending=[];listings=[];seen_urls=set()
    deadline=start+PER_SOURCE

    def log_failure(stage:str,e:Exception) -> None:
        code=e.code if isinstance(e,FetchProblem) else type(e).__name__
        status['failure_details'].append({'stage':stage,'type':code,'detail':str(e)[:180]})

    base=source['url']
    try:
        raw,typ,real_url=fetch(base,source,PDF_CAP if base.lower().endswith('.pdf') else 3_000_000)
        status['http_success']=True;status['adapter_used'].append('requests')
        if 'pdf' in typ or base.lower().endswith('.pdf'):
            listings.append((real_url,source['name'],raw,typ))
        else:
            links,feeds=links_from_html(raw,real_url,source)
            # Some foreign sites publish public metadata with RSS links.
            for feed in feeds[:2]:
                if time.monotonic()>deadline:break
                try:
                    fraw,ftyp,furl=fetch(feed,source,max_bytes=1_500_000)
                    links.extend(links_from_feed(fraw,furl,source))
                    status['adapter_used'].append('rss')
                except FetchProblem as e:log_failure('rss',e)
            if source.get('strategy')=='search_portal':
                for query_url in get_form_search_links(raw,real_url,source):
                    if time.monotonic()>deadline:break
                    try:
                        query_raw,qt,query_real=fetch(query_url,source,max_bytes=2_000_000)
                        results,_=links_from_html(query_raw,query_real,source)
                        links.extend(results)
                        status['adapter_used'].append('official_get_search')
                    except FetchProblem as e:log_failure('public_search_form',e)
            for discovery in source.get('discovery_urls',[])[:2]:
                if time.monotonic()>deadline:break
                try:
                    other_raw,other_type,other_real=fetch(discovery,source,max_bytes=2_000_000)
                    extra,_=links_from_html(other_raw,other_real,source)
                    links.extend(extra)
                    status['adapter_used'].append('verified_public_listing')
                except FetchProblem as e:log_failure('additional_listing',e)
            if len(links)<2 and time.monotonic()<deadline:
                sitemap_candidates=sitemap_links(source)
                links.extend(sitemap_candidates)
                if sitemap_candidates:status['adapter_used'].append('robots_sitemap')
            if source.get('browser_fallback') and (len(links)<2 or len(raw)<4000):
                try:
                    html=browser_render(base,source)
                    more,_=links_from_html(html,base,source)
                    links+=more
                    # Also inspect the rendered document body: some public portals
                    # load regulation text directly without adding a new anchor.
                    if html.strip():
                        raw=html
                        typ='text/html'
                        status['adapter_used'].append('playwright')
                except FetchProblem as e:log_failure('browser',e)
            listings.append((real_url,source['name'],raw,typ))
            # Direct archive entries are still parsed on page with title/paragraph guard.
            for url,title in links:
                if url not in seen_urls:
                    seen_urls.add(url)
                    if url!=canonical_url(real_url):listings.append((url,title,None,''))
            status['links_discovered']=len(links)
    except FetchProblem as e:
        log_failure('entry',e)
        status['status']=e.code;status['status_message']=str(e)
        return [],[],status

    max_pages=max(1,int(source.get('max_articles',5)))
    scanned=0
    for link,title,already,content_type in listings:
        if time.monotonic()>=deadline:
            log_failure('deadline',FetchProblem('time_budget_exhausted','站点时间预算已达上限'))
            break
        # Hard cap total fetched documents per source, first is entry.
        if scanned>=max_pages:break
        try:
            if already is None:
                b,content_type,link=fetch(link,source,PDF_CAP if link.lower().endswith('.pdf') else 3_000_000)
            else:b=already
            status['articles_checked']+=1;scanned+=1
            if content_type.startswith('application/pdf') or link.lower().endswith('.pdf') or b[:4]==b'%PDF':
                body,parts=pdf_text(b)
                doc_title=title if title and title!=source['name'] else (body[:140].split('。')[0] or source['name'])
                status['adapter_used'].append('pdf')
                date=None
            else:
                soup=soup_html(b)
                doc_title=page_title(soup) or title or source['name']
                body,parts=article_text(soup)
                date=parse_date(meta_content(soup,['article:published_time','datePublished','pubdate','DC.date','citation_date','date']) or '')
                if not date:
                    for time_el in soup.select('time[datetime],.date,.pubtime')[:3]:
                        date=parse_date(time_el.get('datetime') or time_el.get_text(' ',strip=True))
                        if date:break
            records,review=analyze_document(source=source,url=link,title=doc_title,
                  body=body,paragraphs=parts,published_at=date,observed_at=now)
            pool.extend(records);pending.extend(review)
        except FetchProblem as e:log_failure(link[:80],e)
        except Exception as e:
            log_failure(link[:80],e)
    # Dedupe within source and bound count; another source may reference same official PDF.
    pool=list({x['id']:x for x in pool}.values())
    pending=list({x['id']:x for x in pending}.values())
    status['accepted']=len(pool);status['pending']=len(pending)
    if status['accepted']>0:status['status']='found'
    elif status['pending']>0:status['status']='needs_review'
    elif source['strategy']=='search_portal':status['status']='search_adapter_required'
    elif not status['links_discovered'] and not pool:status['status']='no_listing_links'
    elif status['failure_details'] and status['articles_checked']<=1:status['status']='partial_failure'
    else:status['status']='no_wind_regulations'
    explanations={
     'found':'抓取到有原文证据的法规候选条款，尚待效力核验',
     'needs_review':'发现通用条款/草案，需人工确认风电适用性',
     'search_adapter_required':'目录/检索门户可打开，但未配置该站真实查询接口，不能宣称持续抓取成功',
     'no_listing_links':'网页可访问但未发现可用详情条目：可能是动态接口或固定介绍页',
     'partial_failure':'部分子页面失败，请查看失败类型',
     'no_wind_regulations':'已扫描候选，但未发现符合强相关筛选的法规条款'}
    status['status_message']=explanations.get(status['status'],'')
    return pool,pending,status
