import sys,unittest,tempfile,json,hashlib
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import policy_scrape as p

DIMS=p.read(p.DIMS,[])
SRCS=p.read(p.SOURCES,[])
HTML_LIST='''<html><body><ul><li>2026-10-10 <a href="/policy/wind-1">关于风电机组并网技术规范的通知</a></li><li>2026-09-11 <a href="/policy/old">关于风电设备的旧标准</a></li><li>2026-10-10 <a href="/home">首页</a></li></ul><a href="https://other.example/a">风电政策通知</a></body></html>'''
HTML_DETAIL='''<html><head><meta property="article:published_time" content="2026-10-10T12:22:00"></head><body><h1>关于风电机组并网技术规范的通知</h1><article><p>2026年10月10日，电网接入与并网技术规范正式发布。</p><p>第一条 对于风力发电机组的并网测试和低电压穿越能力，应当按照适用标准提交技术测试报告，并经主管部门确认后开展接入。</p><p>第二条 具体认证条件和实施期限应以主管部门公布的正式附件为准。项目建设单位应在并网前核对符合性文件及设备技术要求。</p><p>本规定适用于符合条件的风电场并网主体，应保留必要的技术合规记录。</p></article></body></html>'''

class ScraperChecks(unittest.TestCase):
 def test_source_count_and_adapters(self):
  self.assertEqual(len(SRCS),121)
  self.assertEqual(len(DIMS),18)
  self.assertEqual(sum(bool(s['adapter']) for s in SRCS),7)
  self.assertEqual(len({s['url'] for s in SRCS}),121)
  self.assertEqual({a['adapter'] for a in SRCS if a['adapter']},set(p.ADAPTERS))
 def test_date_bounds(self):
  self.assertEqual(p.parse_date('2026年10月13日'),'2026-10-13')
  self.assertEqual(p.parse_date('2026-02-30'),'')
  self.assertEqual(p.parse_date('2026-13-07'),'')
  self.assertEqual(p.parse_date('unavailable'),'')
  self.assertEqual(p.parse_date('13/10/2026'),'2026-10-13')
  self.assertEqual(p.parse_date('13 Oct 2026'),'2026-10-13')
  self.assertEqual(p.parse_date('October 13, 2026'),'2026-10-13')
 def test_title_candidates_and_domain(self):
  links=p.parse_listing(HTML_LIST,'https://fgw.test.example/list')
  self.assertEqual(len(links),2)
  self.assertTrue(all(x['url'].startswith('https://fgw.test.example/') for x in links))
  self.assertEqual(links[0]['date'],'2026-10-10')
 def test_manual_scores(self):
  self.assertIsNone(p.importance(None,[])['score'])
  self.assertIsNone(p.importance({'D':0,'B':0,'C':0,'A':0,'U':0},[])['score'])
  for v,total,priority in [(0,0,'P4'),(1,20,'P4'),(3,60,'P2'),(5,100,'P0')]:
   obj={'D':v,'B':v,'C':v,'A':v,'U':v,'evidence':'已核实的官方原文及企业场景'}
   got=p.importance(obj,[])
   self.assertEqual(got['score'],total)
   self.assertEqual(got['priority'],priority)
  obj={'D':1,'B':1,'C':1,'A':1,'U':1,'evidence':'引用经过核实的官方文件','key_block_verified':True,'business_applies_verified':True,'within_90_days_verified':True,'legal_effect_verified':True}
  got=p.importance(obj,[])
  self.assertTrue(got['override']);self.assertEqual(got['priority'],'P0');self.assertEqual(got['score'],20)
 def test_validated_article_and_excerpts(self):
  src=next(s for s in SRCS if s['name']=='北京市发展和改革委员会')
  obj,err=p.make_record(src,{'title':'关于风电机组并网技术规范的通知','date':'2026-10-10'},HTML_DETAIL,'https://fgw.beijing.gov.cn/policy/wind-1','html',DIMS,p.date(2026,9,14),{})
  self.assertEqual(err,'');self.assertTrue(obj)
  self.assertEqual(obj['published_at'],'2026-10-10')
  self.assertIn('并网测试',obj['source_excerpt'])
  self.assertTrue(obj['dimensions']);self.assertIn('并网技术规范',obj['dimensions'])
  self.assertTrue(obj['impact_design']);self.assertIsNone(obj['importance']['score'])
 def test_no_fake_dates(self):
  src=SRCS[0]
  x,why=p.make_record(src,{'title':'关于风电机组并网技术规范的通知','date':''},HTML_DETAIL.replace('2026-10-10','2020-01-01').replace('2026年10月10日','2020年1月1日'),src['url'],'html',DIMS,p.date(2026,9,14),{})
  self.assertIsNone(x);self.assertIn('早于',why)
  missing=HTML_DETAIL.replace('2026-10-10T12:22:00','unknown').replace('2026年10月10日','近日')
  x,why=p.make_record(src,{'title':'关于风电机组并网技术规范的通知','date':''},missing,src['url'],'html',DIMS,p.date(2026,9,14),{})
  self.assertIsNone(x);self.assertIn('发布日期',why)
 def test_http_error_no_fake_success(self):
  src=SRCS[0]
  with patch.object(p,'fetch',side_effect=ValueError('HTTP 403')):
   with patch.object(p,'browser_fetch',side_effect=ValueError('browser HTTP 403')):
    _,_,st=p.scrape_one(src,DIMS,p.date(2026,9,14),{})
    self.assertEqual(st['status'],'blocked')
    self.assertGreater(len(st['attempts']),0)
 def test_iframe_capture(self):
  u='https://fgw.nmg.gov.cn/zc'
  out=p.extract_iframes('<iframe src="/result/list"></iframe><iframe src="https://evil.example/x"></iframe>',u)
  self.assertEqual(out,['https://fgw.nmg.gov.cn/result/list'])
 def test_source_url_normalization(self):
  self.assertEqual(p.normalize_url('javascript:alert(1)'),'')
  self.assertEqual(p.normalize_url('http://localhost:3000'),'')
  self.assertTrue(p.same_site('https://a.cn','https://www.a.cn'))

 def test_per_clause_importance_review(self):
  src=next(s for s in SRCS if s['name']=='北京市发展和改革委员会')
  official='https://fgw.beijing.gov.cn/policy/wind-1'
  uid='p_'+hashlib.sha256(official.encode()).hexdigest()[:18]
  high={'D':5,'B':5,'C':5,'A':5,'U':5,'evidence':'已核验的官方条文/企业业务边界/期限，需提交核验材料'}
  low={'D':0,'B':0,'C':0,'A':0,'U':0,'evidence':'证据核验完毕：本条无直接义务'}
  manual={uid:{'clauses':{'1':high,'2':low}}}
  obj,err=p.make_record(src,{'title':'关于风电机组并网技术规范的通知','date':'2026-10-10'},HTML_DETAIL,official,'html',DIMS,p.date(2026,9,14),manual)
  self.assertEqual(err,'')
  self.assertEqual(obj['importance']['priority'],'P0')
  self.assertEqual(obj['importance']['score'],100)
  self.assertTrue(obj['clause_scoring'])
  self.assertEqual(obj['clause_scoring'][0]['importance']['score'],100)
  self.assertIn('impact_chain',obj)
  self.assertIn('responsible_teams',obj)
 def test_end_to_end_all_121_offline_fixtures(self):
  # No network: asserts loop coverage, output JSON, 30-day setting and no fabricated articles.
  with tempfile.TemporaryDirectory() as temp:
   dr=Path(temp); dr.mkdir(exist_ok=True)
   def mock_fetch(url):return '<html><body><h1>官方公开目录</h1><p>暂无条目</p></body></html>',url,'html'
   kw={'DATA':dr,'POLICIES':dr/'policies.json','STATUSES':dr/'policy_source_status.json','CANDIDATES':dr/'policy_candidates_review.json','STATE':dr/'policy_crawl_state.json','CHECKPOINTS':dr/'policy_source_checkpoints.json','SCORES':dr/'reviewed_policy_scores.json'}
   from contextlib import ExitStack
   with ExitStack() as stack:
    for key,val in kw.items():stack.enter_context(patch.object(p,key,val))
    stack.enter_context(patch.object(p,'fetch',side_effect=mock_fetch))
    stack.enter_context(patch.object(p,'browser_fetch',side_effect=mock_fetch))
    stack.enter_context(patch.object(p,'BROWSER_ENABLED',True))
    old_disabled=p.LOG.disabled;p.LOG.disabled=True
    try:statuses=p.main(['--mode','auto'])
    finally:p.LOG.disabled=old_disabled
   self.assertEqual(len(statuses),121)
   self.assertEqual(len(set(s['id'] for s in statuses)),121)
   self.assertEqual(json.loads((dr/'policy_crawl_state.json').read_text())['bootstrap_days'],30)
   self.assertEqual(json.loads((dr/'policy_crawl_state.json').read_text())['pending_initialization_count'],121)
   self.assertEqual(len(json.loads((dr/'policy_source_status.json').read_text())),121)
   self.assertEqual(len(json.loads((dr/'policies.json').read_text())),0)
   self.assertEqual(len((dr/'policy_site_acceptance.csv').read_text(encoding='utf-8-sig').splitlines()),122)
   self.assertTrue(any(s['status']=='needs_adapter' for s in statuses))
   self.assertFalse(any(s['status']=='ok_with_results' for s in statuses))
 def test_browser_failure_trace(self):
  src=next(s for s in SRCS if s['adapter']=='iea_policy_search')
  with patch.object(p,'fetch',side_effect=ValueError('HTTP 403')):
   with patch.object(p,'browser_fetch',side_effect=RuntimeError('public portal blocked')):
    _,_,st=p.scrape_one(src,DIMS,p.date(2026,9,14),{})
  self.assertEqual(st['status'],'blocked')
  self.assertTrue(any(a['method']=='browser' for a in st['attempts']))
  self.assertTrue(any('403' in a.get('error','') for a in st['attempts']))

if __name__=='__main__':unittest.main()
