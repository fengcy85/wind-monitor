"""Wind Monitor V6.1: 121 independent site adapters, checkpoints and daily retries.

No CAPTCHA solving, login bypass, access-control circumvention, invented evidence or
speculative legal scores. A successfully fetched home page is not a verified crawl.
"""
from __future__ import annotations
import argparse
import copy
import concurrent.futures as futures
import csv
import hashlib
import io
import json
import logging
import os
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit, parse_qsl, urlencode

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from adapters.registry import load_adapter

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'site' / 'data'
DATA.mkdir(parents=True, exist_ok=True)
SOURCES = ROOT / 'policy_sources.json'
DIMS = ROOT / 'regulatory_dimensions.json'
SCORES = ROOT / 'reviewed_policy_scores.json'
POLICIES = DATA / 'policies.json'
STATUSES = DATA / 'policy_source_status.json'
CANDIDATES = DATA / 'policy_candidates_review.json'
STATE = DATA / 'policy_crawl_state.json'
CHECKPOINTS = DATA / 'policy_source_checkpoints.json'
MAX_DETAILS = max(1, min(int(os.getenv('POLICY_DETAILS_PER_SOURCE','24')),90))
MAX_LIST_PAGES = max(1, min(int(os.getenv('POLICY_LIST_PAGES','5')),12))
MAX_WORKERS = max(1,min(int(os.getenv('POLICY_WORKERS','5')),8))
TIMEOUT = max(3.0,min(float(os.getenv('POLICY_REQUEST_TIMEOUT','8')),20.0))
BROWSER_ENABLED = os.getenv('POLICY_BROWSER_FALLBACK','1') == '1'
LOG = logging.getLogger('policy-v6.1')
logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
_thread = threading.local()

WIND = re.compile(r'风(?:力)?电|风力发电|风机|风力发电机组|风电机组|wind[ -]?(?:power|energy|farm|turbine|generation)|offshore wind|onshore wind|e[oó]lica|éolien|eolico|r[uü]zgar|điện gió|ветр',re.I)
NORM = re.compile(r'法规|政策|条例|办法|规定|规范|规章|法律|通知|意见|指南|规划|标准|认证|政府采购|招投标|招标|并网|电价|环评|管制|细则|条款|准入|grid code|policy|regulat|standard|act\b|decree|law\b|tender|resolution|directive',re.I)
BUSINESS = re.compile(r'风电|可再生能源|新能源|电力|电网|输电|并网|型式认证|电气设备|制造业|发电机|本地化|原产地|外资|贸易|海洋工程|关税|招标|采购|能源|供应链|投资|environmental|electricity|renewable|energy|power|grid|market access|local content|manufactur|certific|supply chain',re.I)
EXCLUDE = re.compile(r'^(首页|更多|点击|返回|上一页|下一页|信息公开|政府信息公开|政策解读|通知公告|联系我们|关于我们|动态|更多信息|免责声明)$',re.I)
DATE = re.compile(r'(?<!\d)(20[1-3]\d)[-./年](0?[1-9]|1[0-2])[-./月](0?[1-9]|[12]\d|3[01])日?(?!\d)')
CLAUSE = re.compile(r'(第[一二三四五六七八九十百零〇0-9]+条|Article\s+\d{1,3}[A-Za-z]?|第[一二三四五六七八九十百0-9]+款)',re.I)
ISSUE = re.compile(r'(征求意见|征求公众意见|草案|公开征求|consultation|draft)',re.I)
EXPIRED = re.compile(r'(失效|废止|已废止|被替代|repealed|revoked|expired)',re.I)
SKIPFILE = re.compile(r'\.(?:png|jpg|jpeg|gif|webp|svg|css|js|zip|rar|doc|docx|xls|xlsx|mp4|ico)(?:$|\?)',re.I)
PDF = re.compile(r'\.pdf(?:$|\?)',re.I)
RECOMMENDED = re.compile(r'推荐性|GB/T|guideline|voluntary',re.I)

# Dimension term groups correspond exactly to the 18 dimension names in original xlsx.
TERMS = [
 ['市场准入','外资','投资限制','准入许可','foreign investment','market access'],
 ['本地化','原产地','本地制造','国产化','local content','localization'],
 ['风机设计','风力发电机组','技术标准','认证','IEC 61400','型式试验','产品标准','testing'],
 ['电网建设','输电','升压站','接入容量','送出','grid connection','transmission'],
 ['并网','低电压穿越','高电压穿越','故障穿越','无功','频率响应','grid code','lvrt'],
 ['电价','新能源开发','可再生能源规划','绿证','ppa','cfd','feed-in tariff','market mechanism'],
 ['招标','投标','采购','供应商资格','bidding','tender','procurement'],
 ['关税','贸易救济','税收','反补贴','反倾销','tariff','tax','anti-dumping'],
 ['大件运输','超限','道路运输','港口吊装','物流','运输许可','logistics'],
 ['环评','生态保护','土地','噪声','鸟类','海域使用','environmental impact','permit'],
 ['航空','雷达','国防','净空','障碍灯','radar','aviation'],
 ['台风','雷击','防腐','沙尘','耐低温','极端风','环境适应','climate condition'],
 ['网络安全','数据安全','scada','数据出境','cybersecurity'],
 ['安全生产','职业安全','消防','施工安全','劳动保护','occupational safety'],
 ['海上风电','海底电缆','海上风机','海洋工程','offshore wind'],
 ['绿色金融','项目融资','担保','贷款','finance','bank guarantee'],
 ['运维','质保','退役','回收','维修','operation and maintenance','decommissioning'],
 ['碳足迹','ESG','供应链','碳排放','碳核算','绿色采购','carbon footprint'],
]
DESIGN_DIMS={2,4,8,10,11,12,13,14,16}
BUSINESS_DIMS={0,1,3,5,6,7,8,9,14,15,16,17}
ADAPTERS={
 'gov_search':['https://www.gov.cn/zhengce/zhengceku/','https://www.gov.cn/zhengce/zuixin/'],
 'gov_document_library':['https://www.gov.cn/zhengce/zhengceku/','https://www.gov.cn/zhengce/zuixin/'],
 'iea_policy_search':['https://www.iea.org/search/policies?q=wind'],
 'samr_standard_search':['https://std.samr.gov.cn/gb/gbQuery.National','https://std.samr.gov.cn/gb/search/gbAdvancedSearch?type=std'],
 'samr_fulltext_search':['https://openstd.samr.gov.cn/bzgk/std/std_list','https://openstd.samr.gov.cn/bzgk/gb/std_list'],
 'nmg_drc_iframe':[],
 'nmg_energy_iframe':[],
}

def read(path,default):
 try:return json.loads(Path(path).read_text(encoding='utf-8'))
 except (OSError,ValueError):return default

def save(path,data):
 p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
 tmp=p.with_suffix(p.suffix+'.tmp')
 tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 tmp.replace(p)

def clean(x):return re.sub(r'\s+',' ',str(x or '')).strip()
def clock():return datetime.now(timezone.utc).isoformat(timespec='seconds')
def normalize_url(url):
 try:
  p=urlsplit(url)
  if p.scheme not in ('https','http') or not p.hostname or p.username or p.password:return ''
  if p.hostname.lower() in ('localhost','127.0.0.1','0.0.0.0') or p.hostname.endswith('.local'):return ''
  return urlunsplit((p.scheme,p.netloc,p.path or '/',p.query,''))
 except ValueError:return ''
def same_site(a,b):
 x=(urlsplit(a).hostname or '').removeprefix('www.').lower()
 y=(urlsplit(b).hostname or '').removeprefix('www.').lower()
 return bool(x and y and (x==y or x.endswith('.'+y) or y.endswith('.'+x) or
   (x.endswith('.gov.cn') and y=='gov.cn') or (y.endswith('.gov.cn') and x=='gov.cn')))

def session():
 if not hasattr(_thread,'s'):
  s=requests.Session()
  s.headers.update({'User-Agent':'WindPolicyMonitor/6.1 (+public policy indexing; research)','Accept':'text/html,application/xhtml+xml,application/pdf,*/*;q=0.2','Accept-Language':'zh-CN,zh;q=0.9,en;q=0.7'})
  retry=Retry(total=1,connect=1,read=1,status=1,backoff_factor=0.35,
              status_forcelist=[429,500,502,503,504],allowed_methods=frozenset(['GET']),
              respect_retry_after_header=False)
  s.mount('https://',HTTPAdapter(max_retries=retry,pool_connections=12,pool_maxsize=12))
  s.mount('http://',HTTPAdapter(max_retries=retry,pool_connections=12,pool_maxsize=12))
  _thread.s=s
 return _thread.s

def fetch(url):
 r=session().get(url,timeout=(min(TIMEOUT,5),TIMEOUT),allow_redirects=True)
 r.raise_for_status()
 if not normalize_url(r.url):raise ValueError('invalid final URL')
 if len(r.content)>7_000_000:raise ValueError('response too large')
 ct=r.headers.get('Content-Type','').lower()
 if 'pdf' in ct or PDF.search(r.url):
  from pypdf import PdfReader
  pdf=PdfReader(io.BytesIO(r.content),strict=False)
  body='\n'.join(p.extract_text() or '' for p in pdf.pages[:20])
  if len(body)<120:raise ValueError('PDF lacks extractable text; needs manual/OCR review')
  return clean(body)[:30000],r.url,'pdf'
 if 'html' not in ct and 'text' not in ct and not r.content.lstrip().startswith(b'<'):
  raise ValueError('unsupported document type: '+ct[:70])
 if not r.encoding or r.encoding.lower() in ('iso-8859-1','ascii'):r.encoding=r.apparent_encoding or 'utf-8'
 return r.text,r.url,'html'

def browser_fetch(url, adapter=""):
 """Render public page normally; no anti-bot bypass / stealth or authenticated access."""
 from playwright.sync_api import sync_playwright
 with sync_playwright() as p:
  browser=p.chromium.launch(headless=True,args=['--no-sandbox'])
  try:
   page=browser.new_page(locale='zh-CN',user_agent='WindPolicyMonitor/6.1 (public compliance monitoring)')
   response=page.goto(url,wait_until='domcontentloaded',timeout=18000)
   page.wait_for_timeout(900)
   # Seven named portal adapters. Use only public UI, not guessed or private APIs.
   configurations={
    'gov_search':{'term':'风电','inputs':('input[name=q]','input[placeholder*=政策]','input[placeholder*=关键词]','input[type=search]'),'buttons':('button:has-text("搜索")','button:has-text("检索")')},
    'gov_document_library':{'term':'风力发电','inputs':('input[placeholder*=政策]','input[name=q]','input[placeholder*=关键词]','input[type=search]'),'buttons':('button:has-text("检索")','button:has-text("搜索")')},
    'samr_standard_search':{'term':'风力发电机组','inputs':('input[placeholder*=标准]','input[placeholder*=编号]','input[type=search]'),'buttons':('button:has-text("查询")','button:has-text("检索")')},
    'samr_fulltext_search':{'term':'风力发电机组','inputs':('input[placeholder*=标准号]','input[placeholder*=标准名称]','input[placeholder*=检索]'),'buttons':('button:has-text("在结果中筛选")','button:has-text("检索")')},
    'nmg_drc_iframe':{'term':'风电','inputs':('input[placeholder*=关键词]','input[placeholder*=搜索]'),'buttons':('button:has-text("搜索")',)},
    'nmg_energy_iframe':{'term':'风电','inputs':('input[placeholder*=关键词]','input[placeholder*=搜索]'),'buttons':('button:has-text("搜索")',)},
   }
   if adapter=='iea_policy_search':
    # The public IEA wind policy index already encodes q=wind in its URL.
    try:page.locator('a[href*="/policies/"]').first.wait_for(timeout=2500)
    except Exception:pass
   elif adapter in configurations:
    opts=configurations[adapter]
    opts['inputs']+=('input[type=text]',)
    for selector in opts['inputs']:
     try:
      node=page.locator(selector).first
      if node.count() and node.is_visible(timeout=400):
       node.fill(opts['term'],timeout=1500)
       clicked=False
       for button in opts['buttons']:
        try:
         b=page.locator(button).first
         if b.count() and b.is_visible(timeout=400):
          b.click(timeout=1300);clicked=True;break
        except Exception:continue
       if not clicked:node.press('Enter',timeout=1300)
       page.wait_for_timeout(1200)
       break
     except Exception:continue
   status=response.status if response else None
   if status and status>=400:raise ValueError('browser HTTP '+str(status))
   return page.content(),page.url,'html'
  finally:browser.close()

def parse_date(x):
 raw=str(x or '')
 # Official foreign portals may use dd/mm/yyyy, dd-mm-yyyy or month-name formats.
 # Demand a full day+month+year to avoid deriving fictional dates from year-only text.
 slash=re.search(r'(?<!\d)([0-3]?\d)[./-]([01]?\d)[./-](20[1-3]\d)(?!\d)',raw)
 if slash:
  try:return date(int(slash.group(3)),int(slash.group(2)),int(slash.group(1))).isoformat()
  except ValueError:return ''
 month_names={name:i for i,names in enumerate(('jan january','feb february','mar march','apr april','may','jun june','jul july','aug august','sep september','oct october','nov november','dec december'),1) for name in names.split()}
 month_re='(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)'
 month_first=re.search(r'(?<!\w)('+month_re+r')\s+([0-3]?\d),?\s+(20[1-3]\d)\b',raw,re.I)
 day_first=re.search(r'(?<!\w)([0-3]?\d)\s+('+month_re+r')\s+(20[1-3]\d)\b',raw,re.I)
 picked=month_first or day_first
 if picked:
  parts=picked.groups()
  mth=parts[0] if month_first else parts[1]
  day=parts[1] if month_first else parts[0]
  year=parts[-1]
  try:return date(int(year),month_names[mth[:3].lower()],int(day)).isoformat()
  except (ValueError,KeyError):return ''
 m=DATE.search(raw)
 if m:
  try:return date(int(m.group(1)),int(m.group(2)),int(m.group(3))).isoformat()
  except ValueError:return ''
 m=re.search(r'(?<!\d)(20[1-3]\d)-(\d\d)-(\d\d)T',str(x or ''))
 return parse_date('-'.join(m.groups())) if m else ''

def publication_date(html,text=''):
 soup=BeautifulSoup(html,'html.parser') if html.lstrip().startswith('<') else None
 hints=[]
 if soup:
  for tag in soup.select('meta[property="article:published_time"],meta[name="pubdate"],meta[name="publishdate"],meta[name="date"],time[datetime],.pubtime,.pubDate,.publish_time,.date,.time')[:16]:
   hints.append(tag.get('content') or tag.get('datetime') or tag.get_text(' ',strip=True))
  for node in soup.find_all(string=re.compile(r'发布(?:日期|时间)|发布日期|Publication Date|Published on',re.I),limit=8):
   hints.append(clean(node.parent.get_text(' ',strip=True))[:150])
 hints.append(text[:350])
 for t in hints:
  d=parse_date(t)
  if d:return d
 return ''

def extract_detail(body,kind,selectors=None):
 if kind=='pdf':return '',clean(body)
 soup=BeautifulSoup(body,'html.parser')
 for tag in soup(['script','style','nav','footer','header','aside','form']):tag.decompose()
 h=soup.find('h1')
 title=clean(h.get_text(' ',strip=True)) if h else ''
 options=[]
 for sel in dict.fromkeys(list(selectors or [])+['article','#UCAP-CONTENT','.TRS_Editor','#zoom','.article-content','.article','#content','.wenzhang','.content','main']):
  for node in soup.select(sel)[:4]:
   t=clean(node.get_text(' ',strip=True))
   if len(t)>120:options.append((len(t),t))
 viable=[(l,t) for l,t in options if l<22000]
 txt=max(viable,key=lambda x:x[0])[1] if viable else clean((soup.body or soup).get_text(' ',strip=True))
 return title,txt[:30000]

def parse_listing(html,base):
 soup=BeautifulSoup(html,'html.parser');links=[];seen=set()
 for a in soup.select('a[href]'):
  t=clean(a.get_text(' ',strip=True)) or clean(a.get('title'))
  if not 7<=len(t)<=190 or EXCLUDE.fullmatch(t):continue
  if not (NORM.search(t) or WIND.search(t) or BUSINESS.search(t)):continue
  u=normalize_url(urljoin(base,a.get('href','')))
  if not u or u in seen or not same_site(base,u) or u==normalize_url(base) or SKIPFILE.search(u):continue
  seen.add(u)
  parent=a.find_parent(['li','tr']) or a.parent
  local=clean(parent.get_text(' ',strip=True))[:350] if parent else ''
  d=parse_date(local)
  score=6*bool(WIND.search(t))+3*bool(NORM.search(t))+int(bool(BUSINESS.search(t)))
  links.append({'title':t,'url':u,'date':d,'rank':score})
 links.sort(key=lambda x:(x['date'],x['rank']),reverse=True)
 return links

def find_next_pages(html,url):
 soup=BeautifulSoup(html,'html.parser');out=[]
 for a in soup.select('a[href]'):
  t=clean(a.get_text(' ',strip=True))
  if t in ('下一页','下页','Next','next','2','3') or a.get('rel')==['next']:
   u=normalize_url(urljoin(url,a.get('href','')))
   if u and same_site(url,u) and u!=url and u not in out:out.append(u)
 return out[:max(0,MAX_LIST_PAGES-1)]

def get_dim_refs(text,dims):
 t=clean(text).lower();score=[]
 for i,(d,terms) in enumerate(zip(dims,TERMS)):
  hits=[k for k in terms if k.lower() in t]
  if hits:score.append((i,min(3,len(hits)),d['name'],hits))
 score.sort(key=lambda x:x[1],reverse=True)
 return score[:5]

def clause_slices(text):
 # Limit to passages containing actual requirement; never fabricate clause numbers.
 sentences=re.split(r'(?<=[。；;\.!?])\s*|\n+',text)
 candidates=[]
 for sentence in sentences:
  sentence=clean(sentence)
  if len(sentence)<35 or len(sentence)>2300:continue
  if (WIND.search(sentence) or BUSINESS.search(sentence)) and (NORM.search(sentence) or re.search(r'应当|必须|不得|要求|实施|适用于| shall | must ',sentence,re.I)):
   candidates.append(sentence)
 if not candidates and len(text)>=130:candidates=[clean(text[:600])]
 return candidates[:4]

def importance(scores,evidence):
 if not isinstance(scores,dict):return {'status':'证据不足，待条款级评分','D':None,'B':None,'C':None,'A':None,'U':None,'score':None,'priority':None,'reason':'缺少经核实的企业适用范围、法律后果及最迟行动日期'}
 values={k:scores.get(k) for k in 'DBCAU'}
 if not all(type(v)==int and 0<=v<=5 for v in values.values()) or not clean(scores.get('evidence')):
  return {'status':'证据不足，待条款级评分',**{k:(v if type(v)==int and 0<=v<=5 else None) for k,v in values.items()},'score':None,'priority':None,'reason':'缺少经核实的五维分数或出处'}
 v=6*values['D']+5*values['B']+4*values['C']+3*values['A']+2*values['U']
 level='P0' if v>=85 else 'P1' if v>=70 else 'P2' if v>=50 else 'P3' if v>=30 else 'P4'
 # Key-block override requires four separate verified boolean fields and cited evidence.
 override=all(scores.get(k) is True for k in ('key_block_verified','business_applies_verified','within_90_days_verified','legal_effect_verified'))
 return {'status':'人工核验评分','score':v,'priority':'P0' if override else level,'base_priority':level,'override':override,**values,'reason':clean(scores['evidence'])[:300]}

# These business-response paths are possible operational checks, never asserted legal duties.
# Every final impact must be traced to the quoted clause and a verified affected entity.
IMPACT_PATHS=[
 ('核对整机及关键零部件的市场认证、许可和技术文件', '核实销售主体、当地代理及准入许可对订单交付的限制', '国际销售、法务与合规、认证与测试'),
 ('核对本地制造零件的技术一致性、质量保证及追溯要求', '核实本地采购比例、建厂、产地证明及投标报价假设', '制造与本地化、供应链与采购、国际销售'),
 ('比对机组设计载荷、部件标准、型式试验报告和认证文件', '复核认证周期、第三方检测费用及客户准入要求', '产品研发、认证与测试、国际销售'),
 ('确认并网配套设备、线路接口及投运条件是否影响交付', '核对电网建设节点对销售回款、施工与交付计划的影响', '电气与控制、项目开发、项目交付'),
 ('复核故障穿越、无功、频率响应及控制软件是否需适配', '核对并网测试责任、接入确认、认证时间及合同里程碑', '电气与控制、认证与测试、国际销售'),
 ('确认是否存在机组效率、计量或调度相关技术条件', '核查绿证、电价、PPA、招标收益测算对客户采购的间接影响', '市场战略、国际销售、项目开发'),
 ('核对投标技术规范、质保测试证明及适用认证要求', '核查供应商资格、评标条件、价格及交付承诺', '国际销售、市场战略、法务与合规'),
 ('确认出口产品编码、材料原产地及认证文件一致性', '核查关税、税率、贸易救济对到岸成本及价格策略的影响', '国际销售、供应链与采购、财务与融资'),
 ('核对风机叶片、塔筒及运输包装的尺寸重量边界', '核查超限运输许可、港口与道路路线、保险及交期', '物流与交付、产品研发、项目开发'),
 ('核对噪声、叶片生态影响和环境监测的设计接口', '核实环评、用地海域和环境许可对项目选址及交付节点的影响', '产品研发、项目开发、法务与合规'),
 ('核查塔高、航标灯、雷达兼容及工程布置需求', '核实航空净空、雷达审批是否限制可销售机型或项目选址', '产品研发、电气与控制、项目开发'),
 ('检查极端风、低温、沙尘、防腐及雷击设计假设', '核查特殊环境型产品定位、成本和质保风险', '产品研发、认证与测试、市场战略'),
 ('复核SCADA、远程运维、数据存储及接口安全控制', '核查数据本地化、网络准入、服务部署及客户合同要求', '电气与控制、运维服务、法务与合规'),
 ('复核安全联锁、施工检修措施及设备技术文件', '核查施工、运维责任划分、人员培训及保险合同', '产品研发、运维服务、法务与合规'),
 ('复核海上机组防腐、海缆接口和船机施工适配', '核查海域使用、施工船期和海上运维服务的交付模式', '产品研发、项目开发、物流与交付'),
 ('确认绿色融资是否要求技术认证或产品信息披露', '核查银行担保、融资条件和项目现金流对客户采购的影响', '财务与融资、国际销售、市场战略'),
 ('复核运维手册、备件、质保周期及退役回收设计', '核查长期运维合同、备件本地化和退役成本承担', '运维服务、产品研发、国际销售'),
 ('复核碳足迹、物料追溯、供应链披露和LCA文档', '核查绿色采购、供应商披露及产品碳足迹对招投标的影响', '供应链与采购、法务与合规、国际销售')
]

def analyze_impact(matched,snippets):
 """Make an evidence-linked, transparently conditional impact chain."""
 lead=matched[0]; idx=lead[0]
 technical,commercial,teams=IMPACT_PATHS[idx]
 passage=snippets[0]['excerpt'] if snippets else ''
 quoted=clean(passage)[:200]
 requirement='官方正文节选：'+quoted
 design='根据“'+quoted[:85]+'…”的文本线索，产品设计核查路径：'+technical+'。是否直接约束整机制造商，待确认。'
 business='根据同一原文线索，商业模式核查路径：'+commercial+'。市场准入或成本后果不能仅凭关键词认定。'
 chain='已获取的条款线索 → 适用主体／实际义务待官方全文核定 → '+technical+'；'+commercial+' → 内部部门开展符合性差距核验。'
 return {'impact_design':design,'impact_business':business,'impact_chain':chain,'impact_evidence':requirement,'affected_entities':'制造商／项目业主／开发商／供应商的直接法律身份待核验','affected_scope':'具体风机类型、陆上/海上、现有/新项目和国家适用范围待核实','consequence':'未从可核实的条款中确认证书、禁售、罚款或并网阻断等具体法律后果','responsible_teams':teams,'uncertainty':'以上为基于关键词定位到原文后的核查路线，不是完成的法律适用性判断。'}

def classify_document(title,text):
 q=title[:150]+' '+text[:600]
 if EXPIRED.search(q):return '历史版本／失效'
 if ISSUE.search(q):return '政策观察／征求意见'
 if RECOMMENDED.search(title) and ('标准' in title or 'GB/T' in title):return '标准（强制性待核验）'
 return '待核验法规候选'

def review_record(src,title,url,reason):
 return {'source_id':src['id'],'source_name':src['name'],'title':title,'url':url,'reason':reason,'checked_at':clock()}

def make_record(src,candidate,html,final,kind,dims,cutoff,manual):
 title,txt=extract_detail(html,kind,load_adapter(src).body_selectors)
 title=title if 7<=len(title)<190 and (WIND.search(title) or NORM.search(title)) else candidate['title']
 pub=publication_date(html,txt) or candidate.get('date','')
 if not pub:return None,'未能证实发布日期（避免把旧法规当近30天）'
 if pub<cutoff.isoformat():return None,'发布日期早于回溯起点'
 if pub>date.today().isoformat():return None,'发布日期晚于运行当天，需人工核验'
 if len(txt)<130:return None,'无法提取法规正文／PDF内容'
 evidence=clause_slices(txt)
 matched=get_dim_refs(' '.join(evidence)+ ' '+title,dims)
 if not matched:return None,'无任何18维度的条款证据'
 # Allow cross-sector regulations, but demand relevant business linkage and substantive policy content.
 if not (WIND.search(txt+' '+title) or (BUSINESS.search(' '.join(evidence)) and NORM.search(txt[:2000]))):
  return None,'尚不能建立风电业务相关影响链条'
 if not (NORM.search(title) or NORM.search(' '.join(evidence))):return None,'无法核实政策规范信号'
 if not evidence:return None,'条款原文证据不足'
 dims_names=[x[2] for x in matched];i=matched[0][0]
 snippets=[]
 for passage in evidence:
  anchor=CLAUSE.search(passage)
  snippets.append({'clause':anchor.group(1) if anchor else '条款编号待核对','excerpt':passage[:380]})
 uid='p_'+hashlib.sha256(final.encode()).hexdigest()[:18]
 anchor=clean(evidence[0])[:180]
 impacts=analyze_impact(matched,snippets)
 scores=manual.get(uid,{})
 # Clause-level scores are accepted only if each clause has separately reviewed evidence.
 reviews=(scores.get('clauses') if isinstance(scores,dict) else {}) or {}
 clause_scores=[]
 for num,clause in enumerate(snippets):
  entry=reviews.get(str(num+1)) or reviews.get(clause['clause']) if isinstance(reviews,dict) else None
  clause_scores.append({'clause':clause['clause'],'excerpt':clause['excerpt'],'importance':importance(entry,[clause])})
 scored=[z['importance'] for z in clause_scores if z['importance']['score'] is not None]
 if scored:
  representative=max(scored,key=lambda x:(-int(x['priority'][1]),x['score'],x['C'],x['U'],x['D'],x['B']))
 else:representative=importance(scores if not reviews and len(snippets)==1 else None,snippets)
 return {
  'id':uid,'title':title,'url':final,'source_id':src['id'],'source_name':src['name'],
  'region':src['region'],'country':src['country'],'province':src.get('province',''),
  'published_at':pub,'discovered_at':clock(),'doc_type':classify_document(title,txt),
  'dimensions':dims_names,'primary_dimension':dims_names[0],
  'source_excerpt':snippets[0]['excerpt'][:340],'clauses':snippets,
  **impacts,'next_step':'核实法律效力、适用主体、条款编号、生效时间、企业目标市场和认证周期后制定应对措施。',
  'importance':representative,'clause_scoring':clause_scores,
  'verification_status':'待人工复核原文和适用范围','article_evidence_available':True,
  'content_hash':hashlib.sha256((title+'\n'+pub+'\n'+'\n'.join(z['excerpt'] for z in snippets)).encode('utf-8')).hexdigest(),
 },''

def adapter_urls(source):
 """Exactly the configured official entries for this site-specific module."""
 adapter=load_adapter(source)
 raw=adapter.entry_urls()
 # Never invent a domain-wide search URL or mistake the homepage for a verified list.
 return list(dict.fromkeys(u for x in raw if (u:=normalize_url(x))))[:5]

def extract_iframes(html,url):
 soup=BeautifulSoup(html,'html.parser');out=[]
 for frm in soup.select('iframe[src],frame[src]'):
  u=normalize_url(urljoin(url,frm.get('src','')))
  if u and same_site(url,u) and u not in out:out.append(u)
 return out[:3]

def _listing_page(html,base,adapter):
 """Parsed official listing plus conservative coverage evidence.

 CSS selectors are source-specific. Falling back to generic anchors keeps
 recall but NEVER proves that a whole site's archive was scanned.
 """
 soup=BeautifulSoup(html,'html.parser')
 anchors=[];selector_hits=[]
 for selector in adapter.list_selectors:
  try: match=soup.select(selector)
  except Exception:continue
  if match:
   selector_hits.append(selector)
   for element in match:
    a=element if element.name=='a' else element.find('a',href=True)
    if a and a.get('href') and a not in anchors:anchors.append(a)
 if not anchors:
  anchors=soup.select('a[href]')
  selector_hits=[]
 links=[];dated_rows=0;seen=set();eligible_rows=0;date_strings=[];unclassified=0
 for a in anchors:
  title=clean(a.get_text(' ',strip=True)) or clean(a.get('title'))
  if not 7<=len(title)<=190 or EXCLUDE.fullmatch(title):continue
  u=normalize_url(urljoin(base,a.get('href','')))
  if not u or u in seen or not same_site(base,u) or u==normalize_url(base) or SKIPFILE.search(u):continue
  seen.add(u)
  # Source-specific date region: use nearest list row, not the complete body.
  parent=a.find_parent(['li','tr','article']) or a.parent
  local=clean(parent.get_text(' ',strip=True))[:420] if parent else title
  for selector in adapter.date_selectors:
   try:
    for node in (parent or a).select(selector)[:2]:
     local+=' '+clean(node.get('datetime') or node.get('content') or node.get_text(' ',strip=True))
   except Exception:continue
  published=parse_date(local)
  eligible_rows+=1
  if published:
   dated_rows+=1
   date_strings.append(published)
  elif NORM.search(title) or WIND.search(title) or BUSINESS.search(title):unclassified+=1
  # A general energy rule can apply to wind turbines even without 'wind' in title.
  if not (NORM.search(title) or WIND.search(title) or BUSINESS.search(title)):continue
  rank=6*bool(WIND.search(title))+3*bool(NORM.search(title))+bool(BUSINESS.search(title))
  links.append({'title':title,'url':u,'date':published,'rank':rank})
 links.sort(key=lambda item:(item['date'],item['rank']),reverse=True)
 # Pagination probes; must not pretend a JS-only next button means exhausted.
 next_urls=[]
 for a in soup.select('a[href]'):
  label=clean(a.get_text(' ',strip=True)).lower()
  rel=a.get('rel') or []
  if ('next' in rel or label in ('下一页','下页','下一頁','next','›','»','2','3') or '下一页' in label or
      'next' in ' '.join(a.get('class') or []).lower()):
   u=normalize_url(urljoin(base,a.get('href','')))
   if u and u!=base and same_site(u,base) and u not in next_urls:next_urls.append(u)
 js_page=bool(soup.select('[onclick*="nextPage"],[onclick*="goPage"],button[aria-label*=Next],a[href="javascript:;"].next'))
 single_page=bool(re.search(r'(?:共\s*1\s*页|第\s*1\s*页\s*(?:，|/|共)\s*1\s*页|Page\s*1\s*of\s*1)',soup.get_text(' ',strip=True),re.I))
 return {'links':links,'selector_matched':bool(selector_hits),'selectors':selector_hits,
         'oldest_date':min(date_strings) if date_strings else '',
         'newest_date':max(date_strings) if date_strings else '',
         'dated_rows':dated_rows,'eligible_rows':eligible_rows,
         'undated_policy_rows':unclassified,'next_urls':next_urls,
         'js_pagination':js_page,'explicit_single_page':single_page,'anchor_total':len(anchors)}

def _try_entry(url, legacy_adapter, should_render, attempts):
 """Try public HTTP then normal browser render only when necessary; no bypass."""
 try:
  html,actual,kind=fetch(url)
  attempts.append({'url':url,'method':'http','status':'read','redirect':actual})
  return html,actual,kind
 except Exception as exc:
  error=type(exc).__name__+': '+clean(str(exc))[:150]
  attempts.append({'url':url,'method':'http','status':'failed','error':error})
  if not (BROWSER_ENABLED and should_render):return None
  try:
   html,actual,kind=browser_fetch(url,legacy_adapter)
   attempts.append({'url':url,'method':'browser','status':'read','redirect':actual})
   return html,actual,kind
  except Exception as be:
   attempts.append({'url':url,'method':'browser','status':'failed',
                    'error':type(be).__name__+': '+clean(str(be))[:150]})
   return None

def attachment_urls(html,base):
 """Public PDFs linked from an official detail page, not external mirrors."""
 soup=BeautifulSoup(html,'html.parser');result=[]
 for a in soup.select('a[href]'):
  href=a.get('href','');label=clean(a.get_text(' ',strip=True))
  if not (PDF.search(href) or (re.search(r'附件|下载|全文|pdf|原文',label,re.I) and
      re.search(r'\.pdf(?:$|\?)|downfile\.jsp|/download/pdf',href,re.I))):continue
  url=normalize_url(urljoin(base,href))
  if url and same_site(base,url) and url not in result:result.append(url)
 return result[:2]


def _result_status(attempts, found, details_errors, limit_hit, coverage_complete, any_listing,
                   any_parsed, has_undated, has_incomplete_pagination):
 messages=' '.join(str(a.get('error','')) for a in attempts)
 if not any(a.get('status')=='read' for a in attempts):
  if re.search(r'403|429|blocked|forbidden',messages,re.I):return 'blocked','官方访问受限；保留失败证据，不能绕过权限'
  if re.search(r'connect|unreachable|timed out|timeout|network',messages,re.I):return 'connection_error','官方来源连接失败或超时，后续自动补查'
  return 'error','官方来源无法读取，后续仍重试'
 if coverage_complete:
  if found:return 'ok_with_results','有真实详情正文与发布日期证据，且已获得本站完整时间窗的分页覆盖证据'
  return 'ok_no_recent','该来源在本次时间窗内未取得可纳入的法规；分页与日期覆盖已核验'
 if details_errors:return 'partial_coverage','法规详情下载失败，存在遗漏风险；初始化或增量游标不推进'
 if limit_hit:return 'partial_coverage','达到详情或目录翻页限额，不能判定完整覆盖'
 if has_incomplete_pagination:return 'partial_coverage','分页加载或日期区间不完整，不能推进已核验游标'
 if has_undated:return 'partial_coverage','列表候选发布日期不完整，不能证明覆盖所需时间段'
 if found:return 'partial_coverage','取得带原文证据的法规，但目录仍未完整覆盖，继续补查'
 if not any_listing or not any_parsed:return 'needs_adapter','专站目录选择器或公开检索尚未识别到有效列表，等待真实网页结构核验'
 return 'partial_coverage','已读取部分目录，但未获取完整可靠的日期覆盖证据'

def scrape_one(src,dims,cutoff,manual):
 """Audit one configured official source, conservatively verifying calendar coverage.

 Caller owns the per-source checkpoint; this function NEVER calls a site successful
 simply because an HTTP 200 page was received.
 """
 adapter=load_adapter(src)
 at=clock();attempts=[];found={};reviews=[]
 links_by_url={};any_listing=False;any_parsed=False;details_errors=0
 rows_with_date=0;rows_total=0;undated_policy_rows=0
 oldest_dates=[];more_unfetched=False;limit_hit=False;selector_used=False
 visited_pages=set();pages_count=0;primary_read=False;reliable_entry=False;explicit_end=False
 for start_url in adapter_urls(src):
  # Do not mix pages from independently designed interfaces: a successful
  # primary listing takes precedence over fallbacks.
  if reliable_entry:break
  discovered=[start_url];queue_count=0;entry_read=False;entry_results=0
  while discovered and queue_count<MAX_LIST_PAGES:
   entry_url=discovered.pop(0)
   if entry_url in visited_pages:continue
   visited_pages.add(entry_url);queue_count+=1;pages_count+=1
   fetched=_try_entry(entry_url,src.get('adapter',''),queue_count==1,attempts)
   if fetched is None:continue
   html,actual,kind=fetched
   if not same_site(entry_url,actual):
    attempts.append({'url':entry_url,'method':'validation','status':'failed','error':'非同官方域名的跳转被拒绝'})
    continue
   entry_read=True
   if kind=='pdf':
    candidate={'title':src['name'],'date':''}
    obj,err=make_record(src,candidate,html,actual,kind,dims,cutoff,manual)
    if obj:found[obj['id']]=obj
    elif err and len(reviews)<20:reviews.append(review_record(src,src['name'],actual,err))
    # Individual documents are not evidence of an entire month's archive.
    continue
   page=_listing_page(html,actual,adapter)
   # For a public search application, rendered DOM often contains the only results.
   if (not page['links'] and BROWSER_ENABLED and queue_count==1 and
       (adapter.config['needs_browser_ui'] or re.search(r'__NEXT_DATA__|__NUXT__|webpack|vue(?:\.runtime)?\.js|data-reactroot',html,re.I)) and
       not any(a['method']=='browser' and a['status']=='read' and a['url']==entry_url for a in attempts)):
    try:
     rendered,render_url,kind2=browser_fetch(entry_url,src.get('adapter',''))
     attempts.append({'url':entry_url,'method':'public_ui_search','status':'read','redirect':render_url})
     if kind2=='html' and same_site(entry_url,render_url):
      trial=_listing_page(rendered,render_url,adapter)
      if trial['anchor_total']>page['anchor_total']:page=trial;html=rendered
    except Exception as exc:
     attempts.append({'url':entry_url,'method':'public_ui_search','status':'failed',
                      'error':type(exc).__name__+': '+clean(str(exc))[:150]})
   if adapter.config['allow_iframe']:
    for iframe_url in extract_iframes(html,actual):
     framed=_try_entry(iframe_url,src.get('adapter',''),False,attempts)
     if framed and framed[2]=='html' and same_site(iframe_url,framed[1]):
      sub=_listing_page(framed[0],framed[1],adapter)
      page['links'].extend(sub['links']);page['dated_rows']+=sub['dated_rows']
      page['eligible_rows']+=sub['eligible_rows'];page['undated_policy_rows']+=sub['undated_policy_rows']
      if sub['oldest_date']:
       page['oldest_date']=min([d for d in (page['oldest_date'],sub['oldest_date']) if d])
      page['selector_matched']=page['selector_matched'] or sub['selector_matched']
      page['next_urls']+=sub['next_urls']
   explicit_end=explicit_end or page['explicit_single_page']
   any_listing=any_listing or (page['eligible_rows']>0 and bool(page['selector_matched']))
   any_parsed=any_parsed or page['eligible_rows']>0
   selector_used=selector_used or page['selector_matched']
   rows_with_date+=page['dated_rows'];rows_total+=page['eligible_rows']
   undated_policy_rows+=page['undated_policy_rows']
   if page['oldest_date']:oldest_dates.append(page['oldest_date'])
   for cand in page['links']:
    if cand['url'] not in links_by_url:
     links_by_url[cand['url']]=cand;entry_results+=1
   if page['js_pagination']:
    more_unfetched=True
   next_urls=[x for x in page['next_urls'] if x not in visited_pages and x not in discovered]
   # When the listing proves there are older dated items, we can stop following
   # additional pages only if every recognized row is dated, otherwise incomplete.
   if page['oldest_date'] and page['oldest_date']<cutoff.isoformat() and page['eligible_rows']==page['dated_rows'] and page['selector_matched']:
    # no need for pages older than cutoff
    next_urls=[]
   discovered.extend(next_urls[:max(0,MAX_LIST_PAGES-queue_count)])
   if page['next_urls'] and queue_count>=MAX_LIST_PAGES and next_urls:more_unfetched=True
  if discovered:more_unfetched=True
  if entry_read and (entry_results or any_listing):
   reliable_entry=True;primary_read=True
   # If the entry is a generic homepage, it cannot prove the month was indexed.
   if (urlsplit(start_url).path or '/')=='/' and adapter.strategy not in ('legal_database','iea_public_policy_index'):
    reliable_entry=False
   if adapter.strategy=='official_index_fallback' and re.search(r'\.s?html?$',urlsplit(start_url).path,re.I):
    reliable_entry=False
   if reliable_entry:break
 # Process ALL dated candidates in the requested window, not just a random
 # front-page slice. Exhaustion/max details errors leave the checkpoint pending.
 scanned=0;dates_missing=0;unique_candidates=sorted(links_by_url.values(),key=lambda x:(x['date'],x['rank']),reverse=True)
 for candidate in unique_candidates:
  if candidate['date'] and candidate['date']<cutoff.isoformat():continue
  if scanned>=MAX_DETAILS:limit_hit=True;break
  scanned+=1
  fetched=_try_entry(candidate['url'],src.get('adapter',''),False,attempts)
  if fetched is None:
   details_errors+=1
   if len(reviews)<30:reviews.append(review_record(src,candidate['title'],candidate['url'],'详情访问失败'))
   continue
  html,final,kind=fetched
  if not same_site(candidate['url'],final):
   details_errors+=1
   if len(reviews)<30:reviews.append(review_record(src,candidate['title'],final,'跨站点详情跳转，待验证官方关系'))
   continue
  obj,reason=make_record(src,candidate,html,final,kind,dims,cutoff,manual)
  # Many official portals only publish a short notice; binding legal text sits
  # in a public PDF attachment. Read it as evidence if HTML did not qualify.
  if not obj and kind=='html':
   attached=attachment_urls(html,final)
   for pdf_url in attached:
    result=_try_entry(pdf_url,src.get('adapter',''),False,attempts)
    if result is None:
     details_errors+=1
     continue
    pdf_text,pdf_final,pdf_kind=result
    if pdf_kind!='pdf' or not same_site(final,pdf_final):continue
    obj,reason=make_record(src,candidate,pdf_text,pdf_final,pdf_kind,dims,cutoff,manual)
    if obj:break
  if obj:found[obj['id']]=obj
  elif reason and len(reviews)<30:
   reviews.append(review_record(src,candidate['title'],final,reason))
  if reason and '发布日期' in reason:dates_missing+=1
 older_seen=bool(oldest_dates and min(oldest_dates)<cutoff.isoformat())
 all_rows_dated=rows_total>0 and rows_with_date==rows_total and undated_policy_rows==0
 trustworthy_pagination=(older_seen or explicit_end)
 # Reading some rows is not sufficient; require source-specific selector,
 # consistent dates, and no outstanding listing/detail/limit failures.
 complete=bool(reliable_entry and selector_used and all_rows_dated and trustworthy_pagination
               and not more_unfetched and not details_errors and not limit_hit and not dates_missing
               and adapter.strategy!='document_entry_only' and
               (older_seen or explicit_end))
 # Per-source configured selectors are hypotheses until independently verified;
 # a newly generated profile MUST NOT self-certify simply by finding a few links.
 # Real proof requires a time-window boundary or an exhaustive finite listing.
 if complete and not older_seen and pages_count==1:
  # A single homepage-like listing cannot prove monthly coverage.
  entry_path=urlsplit(adapter.config['official_url']).path
  if not entry_path or entry_path=='/':complete=False
 status,message=_result_status(attempts,found,details_errors,limit_hit,complete,any_listing,any_parsed,
                                undated_policy_rows>0 or dates_missing>0,more_unfetched)
 record={'id':src['id'],'name':src['name'],'status':status,'checked_at':at,
         'found_count':len(found),'candidate_links':len(links_by_url),
         'candidate_dated_links':sum(bool(x['date']) for x in links_by_url.values()),
         'listing_rows_with_date':rows_with_date,'listing_rows_total':rows_total,
         'listing_pages':pages_count,'oldest_listing_date':min(oldest_dates) if oldest_dates else '',
         'explicit_end_of_list':explicit_end,
         'detail_errors':details_errors,'detail_limit_hit':limit_hit,
         'coverage_complete':complete,'coverage_evidence':('已识别原始栏目、逐条日期及翻页边界' if complete else '缺少完整时间窗覆盖证据'),
         'message':message,'attempts':attempts,'window_start':cutoff.isoformat(),
         'window_end':date.today().isoformat(),'adapter':src['id'],
         'adapter_strategy':adapter.strategy,'adapter_live_verified':adapter.config['selectors_live_verified']}
 return list(found.values()),reviews,record


def export_csv(rows,filename,headers):
 with open(DATA/filename,'w',newline='',encoding='utf-8-sig') as f:
  w=csv.writer(f);w.writerow(headers)
  for r in rows:w.writerow([r.get(k,'') if not isinstance(r.get(k),list) else '、'.join(map(str,r[k])) for k in headers])


def make_checkpoint(source, previous, today):
 """Per-source fixed bootstrap start prevents silent loss after days of failures."""
 prev=copy.deepcopy(previous) if isinstance(previous,dict) else {}
 prev.setdefault('source_id',source['id'])
 prev.setdefault('first_seen_on',today.isoformat())
 prev.setdefault('initial_window_start',(date.fromisoformat(prev['first_seen_on'])-timedelta(days=29)).isoformat())
 prev.setdefault('initialized',False)
 prev.setdefault('last_verified_date',None)
 prev.setdefault('last_verified_at',None)
 prev.setdefault('consecutive_failures',0)
 prev.setdefault('total_attempts',0)
 prev.setdefault('last_error','')
 return prev


def window_for(checkpoint,today,overlap_days=2):
 """Returns (phase, inclusive cutoff); keep original baseline for failed initializers."""
 if checkpoint['initialized'] and checkpoint.get('last_verified_date'):
  last=date.fromisoformat(checkpoint['last_verified_date'])
  return ('daily_increment', min(today,last-timedelta(days=overlap_days)))
 return ('initial_month',date.fromisoformat(checkpoint['initial_window_start']))


def advance_checkpoint(checkpoint,status,today):
 cp=copy.deepcopy(checkpoint);cp['total_attempts']+=1
 cp['last_attempt_at']=status.get('checked_at') or clock()
 cp['last_status']=status['status']
 if status.get('coverage_complete') and status['status'].startswith('ok_'):
  cp['initialized']=True;cp['last_verified_date']=today.isoformat()
  cp['last_verified_at']=clock();cp['consecutive_failures']=0;cp['last_error']=''
 else:
  cp['consecutive_failures']+=1;cp['last_error']=status.get('message','')
 return cp


def merge_legal_records(old,items,source_by_id):
 """Non-destructive URL dedup: same content stays; changed evidence updates in place."""
 by_url={};counts={'inserted':0,'updated':0,'unchanged':0}
 if isinstance(old,dict):
  old=next((old[k] for k in ('policies','items','records','articles') if isinstance(old.get(k),list)), [])
 for x in (old if isinstance(old,list) else []):
  if not isinstance(x,dict) or not x.get('url') or not x.get('title'):continue
  y=copy.deepcopy(x);source=source_by_id.get(y.get('source_id'),{})
  y.setdefault('country',source.get('country','待核实'))
  y.setdefault('region',source.get('region','待核实'))
  y.setdefault('source_name',source.get('name','历史来源'))
  y.setdefault('source_excerpt',y.get('excerpt',''))
  y.setdefault('dimensions',[]);y.setdefault('published_at','')
  y.setdefault('doc_type','历史记录／待核验')
  y.setdefault('impact_design','需依据法规条款重新开展产品影响分析')
  y.setdefault('impact_business','需依据法规条款重新开展商业模式影响分析')
  y.setdefault('importance',importance(None,[]))
  y.setdefault('verification_status','历史导入，待重新核验')
  by_url[normalize_url(y['url']) or y['url']]=y
 for new in items:
  key=normalize_url(new['url']) or new['url']
  if key in by_url:
   old=by_url[key]
   fields=('title','published_at','source_excerpt','dimensions','impact_design','impact_business')
   if any(old.get(k)!=new.get(k) for k in fields):
    # Preserve manually-reviewed scores until independent evidence review;
    # manual reviewed input is always re-applied by make_record.
    if old.get('importance',{}).get('status')=='人工核验评分' and new.get('importance',{}).get('score') is None:
     if old.get('source_excerpt')==new.get('source_excerpt'):
      new['importance']=old['importance'];new['clause_scoring']=old.get('clause_scoring',[])
     else:
      new['prior_importance_pending_recheck']=old['importance']
      new['importance']=importance(None,[])
      new['clause_scoring']=[]
    new['originally_discovered_at']=old.get('originally_discovered_at') or old.get('discovered_at')
    new['updated_at']=clock();counts['updated']+=1;by_url[key]=new
   else:counts['unchanged']+=1
  else:
   counts['inserted']+=1;new['originally_discovered_at']=new.get('discovered_at',clock());by_url[key]=new
 policies=sorted(by_url.values(),key=lambda x:(x.get('published_at') or '',x.get('discovered_at') or ''),reverse=True)
 return policies,counts


def main(argv=None):
 p=argparse.ArgumentParser(description='V6.1: 121 source-specific adapters, initial 30 days per site then daily incremental')
 p.add_argument('--mode',choices=('auto',),default='auto',help='Always auto; no global --days setting')
 p.add_argument('--offline-fixtures',action='store_true',help='Only for tests: no network')
 p.add_argument('--prepare',action='store_true',help='Write first-seen per-source checkpoints without network; used by Actions')
 a=p.parse_args(argv)
 if a.offline_fixtures:raise SystemExit('No fake network results are generated')
 sources=read(SOURCES,[]);dims=read(DIMS,[])
 if len(sources)!=121 or len({s['id'] for s in sources})!=121 or len(dims)!=18:
  raise SystemExit('Expected 121 distinct official sources and 18 regulatory dimensions')
 # Refuse silent fallback to a generic parser when even one individual module is absent.
 adapter_map={src['id']:load_adapter(src) for src in sources}
 if len(adapter_map)!=121 or not all(a.is_site_specific() for a in adapter_map.values()):
  raise SystemExit('Missing a source-specific adapter module')
 today=date.today();manual=read(SCORES,{})
 saved=read(CHECKPOINTS,{})
 if not isinstance(saved,dict):saved={}
 previous_states=saved.get('sources',{}) if isinstance(saved.get('sources',{}),dict) else {}
 checkpoints={s['id']:make_checkpoint(s,previous_states.get(s['id']),today) for s in sources}
 if a.prepare:
  if len(previous_states)!=len(checkpoints) or not CHECKPOINTS.exists():
   save(CHECKPOINTS,{'version':1,'updated_at':clock(),'bootstrap_days':30,'daily_overlap_days':2,'sources':checkpoints})
  LOG.info('Prepared %s source-specific checkpoints without network',len(checkpoints))
  return checkpoints
 if len(previous_states)!=len(checkpoints) or not CHECKPOINTS.exists():
  # Journal the first-seen date before long-running work, not after all 121 calls.
  save(CHECKPOINTS,{'version':1,'updated_at':clock(),'bootstrap_days':30,'daily_overlap_days':2,'sources':checkpoints})
 jobs=[]
 for source in sources:
  cp=checkpoints[source['id']]
  phase,cutoff=window_for(cp,today)
  jobs.append((source,phase,cutoff))
 LOG.info('V6.1: 121 per-site checkpoints; bootstrap=%s daily=%s workers=%s',
          sum(x[1]=='initial_month' for x in jobs),sum(x[1]=='daily_increment' for x in jobs),MAX_WORKERS)
 statuses=[];reviewed=[];items=[]
 with futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
  tasks={pool.submit(scrape_one,src,dims,cutoff,manual):(src,phase,cutoff) for src,phase,cutoff in jobs}
  for n,task in enumerate(futures.as_completed(tasks),1):
   src,phase,cutoff=tasks[task]
   try:docs,reviews,status=task.result()
   except Exception as exc:
    docs,reviews=[],[]
    status={'id':src['id'],'name':src['name'],'status':'error','checked_at':clock(),
            'found_count':0,'candidate_links':0,'message':'运行异常 '+type(exc).__name__+': '+str(exc)[:200],
            'attempts':[],'window_start':cutoff.isoformat(),'window_end':today.isoformat(),
            'coverage_complete':False,'adapter':src['id'],'adapter_strategy':adapter_map[src['id']].strategy}
   status['phase']=phase
   checkpoint=advance_checkpoint(checkpoints[src['id']],status,today)
   checkpoints[src['id']]=checkpoint
   status['initialized']=checkpoint['initialized']
   status['bootstrap_started_on']=checkpoint['first_seen_on']
   status['bootstrap_window_start']=checkpoint['initial_window_start']
   status['last_verified_date']=checkpoint.get('last_verified_date')
   status['consecutive_failures']=checkpoint['consecutive_failures']
   statuses.append(status);reviewed.extend(reviews);items.extend(docs)
   LOG.info('%s/121 [%s] [%s] %s +%s, verified=%s',n,phase,status['status'],src['name'],len(docs),status.get('coverage_complete',False))
 assert len(statuses)==121 and len({s['id'] for s in statuses})==121
 order={s['id']:i for i,s in enumerate(sources)}
 statuses.sort(key=lambda st:order[st['id']])
 records,changes=merge_legal_records(read(POLICIES,[]),items,{s['id']:s for s in sources})
 # Persist all legal records; do not truncate historical results to a hidden cap.
 save(POLICIES,records)
 save(STATUSES,statuses)
 save(CANDIDATES,reviewed[:3000])
 save(CHECKPOINTS,{'version':1,'updated_at':clock(),'bootstrap_days':30,'daily_overlap_days':2,'sources':checkpoints})
 initialized_count=sum(cp['initialized'] for cp in checkpoints.values())
 counts={code:sum(st['status']==code for st in statuses) for code in sorted({st['status'] for st in statuses})}
 state={'version':'V6.1','last_run':clock(),'configured_source_count':121,'attempted_source_count':121,
        'initial_phase_count':sum(s['phase']=='initial_month' for s in statuses),
        'incremental_phase_count':sum(s['phase']=='daily_increment' for s in statuses),
        'initialized_source_count':initialized_count,'pending_initialization_count':121-initialized_count,
        'bootstrap_days':30,'daily_overlap_days':2,'status_counts':counts,'change_counts':changes}
 save(STATE,state)
 rows=[{'法规名称':x.get('title',''),'所属国家区域':x.get('country','')+' / '+x.get('province',''),
        '发布日期':x.get('published_at',''),'法规原文节选':x.get('source_excerpt',''),
        '法规维度标签':'、'.join(x.get('dimensions') or []),'产品设计影响':x.get('impact_design',''),
        '商业模式影响':x.get('impact_business',''),'重要度':(x.get('importance') or {}).get('priority') or '待条款评分',
        '法律状态':x.get('doc_type',''),'原文链接':x.get('url','')} for x in records]
 heads=['法规名称','所属国家区域','发布日期','法规原文节选','法规维度标签','产品设计影响','商业模式影响','重要度','法律状态','原文链接']
 export_csv(rows,'policies.csv',heads)
 acceptance=[]
 for src,st in zip(sources,statuses):
  attempts_errors=[a.get('error','') for a in st.get('attempts',[]) if a.get('status')=='failed']
  acceptance.append({'网站名称':src['name'],'当前状态':st['status'],
    '失败原因':st['message']+('；'+str(attempts_errors[:2]) if attempts_errors else ''),
    '需要怎么适配':st.get('adapter_strategy')+' / '+src['id']+'；核对本源列表日期、全文、附件和翻页',
    '修复后测试结果':('完整窗口覆盖可审计' if st.get('coverage_complete') else '未通过完整时间窗验收；仍需线上复测或适配'),
    '官方网站':src['url'],'专站适配器':st['adapter'],'策略':st['adapter_strategy'],
    '阶段':st['phase'],'初始化完成':st['initialized'],'初始窗口起点':st['bootstrap_window_start'],
    '本轮起点':st['window_start'],'最后核验日期':st.get('last_verified_date') or '',
    '日期覆盖核验':st.get('coverage_complete',False),'发布日期有效行数':st.get('listing_rows_with_date',0),
    '目录总行数':st.get('listing_rows_total',0),'详情错误数':st.get('detail_errors',0),
    '法规候选数':st['found_count'],'连续失败次数':st.get('consecutive_failures',0),
    '检查时间UTC':st['checked_at'],'详细访问轨迹':json.dumps(st.get('attempts',[]),ensure_ascii=False)})
 export_csv(acceptance,'policy_site_acceptance.csv',list(acceptance[0]))
 save(DATA/'policy_site_acceptance.json',acceptance)
 LOG.info('Completed 121/121, initialized %s/121, inserted=%s updated=%s unchanged=%s; false successes prohibited',
          initialized_count,changes['inserted'],changes['updated'],changes['unchanged'])
 return statuses

if __name__=='__main__':main()
