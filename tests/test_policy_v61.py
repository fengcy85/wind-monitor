"""V6.1 reliability checks. Synthetic pages assert behavior, not site reachability."""
from __future__ import annotations
import json
import csv
import tempfile
import unittest
from datetime import date,timedelta
from pathlib import Path
from unittest.mock import patch
from contextlib import ExitStack
from urllib.parse import urlsplit

import policy_scrape as p

SOURCES=p.read(p.SOURCES,[])
DIMS=p.read(p.DIMS,[])


def detail_markup(when):
 return f'''<html><head><meta property="article:published_time" content="{when}T10:00:00"></head>
 <body><h1>关于风电机组并网检测和认证技术规范的通知</h1><article><p>发布日期：{when}</p>
 <p>第一条 风力发电机组应当符合电网建设及并网技术规范的要求；制造商须依据适用标准提交型式试验报告及并网测试文件，保障风电设备可靠安全运行。</p>
 <p>第二条 对海上风电设备、并网系统、施工运输及相关供应商应当建立产品质量保障记录，明确各主体责任和文件审查方式，实施风险分级。</p>
 </article></body></html>'''


def listing_markup(host,new_date,older_date):
 return f'''<html><body><table><tr><th>政策法规清单</th></tr>
 <tr><td><a href="https://{host}/doc/wind-new.html">关于风电并网标准和认证的新通知</a></td><td>{new_date}</td></tr>
 <tr><td><a href="https://{host}/doc/wind-old.html">关于风电并网标准实施的旧通知</a></td><td>{older_date}</td></tr>
 </table><span>分页结束</span></body></html>'''


class V61Checkpoints(unittest.TestCase):
 def test_modules_one_per_121_urls_and_dimensions(self):
  self.assertEqual(len(SOURCES),121)
  self.assertEqual(len(DIMS),18)
  self.assertEqual(len({s['id'] for s in SOURCES}),121)
  for src in SOURCES:
   obj=p.load_adapter(src)
   self.assertEqual(obj.id,src['id'])
   self.assertTrue(obj.is_site_specific())
   self.assertTrue((p.ROOT/'adapters'/'sites'/(src['id']+'.py')).is_file())
   self.assertFalse(obj.config['selectors_live_verified'])
  self.assertEqual(sum(s['region']=='中国' for s in SOURCES),100)
  self.assertEqual(sum(s['region']=='海外/国际' for s in SOURCES),21)

 def test_first_month_is_fixed_per_site_when_failures_span_more_than_month(self):
  src=SOURCES[0]
  d=date(2026,10,13)
  cp=p.make_checkpoint(src,None,d)
  self.assertEqual(cp['initial_window_start'],'2026-09-14')
  self.assertEqual(p.window_for(cp,d),('initial_month',date(2026,9,14)))
  st={'status':'connection_error','message':'网络中断','checked_at':'2026-10-13T00:00:00Z','coverage_complete':False}
  cp=p.advance_checkpoint(cp,st,d)
  self.assertEqual(cp['consecutive_failures'],1)
  for delta in (1,3,15,32,61):
   phase,cutoff=p.window_for(cp,d+timedelta(days=delta))
   self.assertEqual(phase,'initial_month')
   self.assertEqual(cutoff,date(2026,9,14))
  self.assertFalse(cp['initialized'])

 def test_success_changes_only_own_checkpoint_and_next_day_daily_overlap(self):
  d=date(2026,10,13)
  one=p.make_checkpoint(SOURCES[0],{},d)
  two=p.make_checkpoint(SOURCES[1],{},d)
  ok={'status':'ok_no_recent','message':'有完整日期和分页证据','checked_at':'2026-10-13T00:00:00Z','coverage_complete':True}
  one=p.advance_checkpoint(one,ok,d)
  self.assertTrue(one['initialized'])
  self.assertFalse(two['initialized'])
  self.assertEqual(p.window_for(one,d+timedelta(days=1)),('daily_increment',d-timedelta(days=2)))
  self.assertEqual(p.window_for(two,d+timedelta(days=1)),('initial_month',d-timedelta(days=29)))
  bad={'status':'partial_coverage','message':'翻页不足','checked_at':'2026-10-14T00:00:00Z','coverage_complete':False}
  one=p.advance_checkpoint(one,bad,d+timedelta(days=1))
  self.assertEqual(one['last_verified_date'],d.isoformat())
  self.assertEqual(p.window_for(one,d+timedelta(days=5))[1],d-timedelta(days=2))

 def test_days_inclusive_month_and_new_year_boundary(self):
  for today,start in [(date(2026,1,2),date(2025,12,4)),(date(2026,3,1),date(2026,1,31)),(date(2026,10,13),date(2026,9,14))]:
   cp=p.make_checkpoint(SOURCES[0],{},today)
   self.assertEqual(date.fromisoformat(cp['initial_window_start']),start)
   self.assertEqual((today-start).days+1,30)

 def test_cannot_mark_ok_if_http200_is_only_a_homepage(self):
  src=SOURCES[0]
  with patch.object(p,'fetch',return_value=('<html><body><h1>首页</h1>欢迎访问</body></html>',src['url'],'html')):
   with patch.object(p,'BROWSER_ENABLED',False):
    records,review,st=p.scrape_one(src,DIMS,date.today()-timedelta(days=29),{})
  self.assertEqual(records,[])
  self.assertFalse(st['coverage_complete'])
  self.assertIn(st['status'],('partial_coverage','needs_adapter'))

 def test_configured_listing_two_dates_and_official_details(self):
  src=next(s for s in SOURCES if s['name']=='北京市发展和改革委员会')
  host=urlsplit(src['url']).hostname
  today=date.today();fresh=(today-timedelta(days=2)).isoformat();older=(today-timedelta(days=40)).isoformat()
  def fake_fetch(url):
   if 'wind-new.html' in url:return detail_markup(fresh),url,'html'
   if 'wind-old.html' in url:return detail_markup(older),url,'html'
   return listing_markup(host,fresh,older),url,'html'
  with patch.object(p,'fetch',side_effect=fake_fetch):
   with patch.object(p,'BROWSER_ENABLED',False):
    records,review,st=p.scrape_one(src,DIMS,today-timedelta(days=29),{})
  self.assertEqual(st['status'],'ok_with_results')
  self.assertTrue(st['coverage_complete'])
  self.assertEqual(len(records),1)
  self.assertIn('并网',records[0]['source_excerpt'])
  self.assertEqual(records[0]['country'],'中国')
  self.assertTrue(records[0]['dimensions'])
  self.assertIn('产品',records[0]['impact_design'])
  self.assertIsNone(records[0]['importance']['score'])
  self.assertEqual(st['listing_rows_with_date'],2)

 def test_detail_403_blocks_checkpoint_even_if_listing_looks_complete(self):
  src=next(s for s in SOURCES if s['name']=='北京市发展和改革委员会')
  host=urlsplit(src['url']).hostname
  today=date.today();fresh=(today-timedelta(days=1)).isoformat();older=(today-timedelta(days=50)).isoformat()
  def fake_fetch(url):
   if 'wind-new.html' in url:raise ValueError('HTTP 403 Forbidden')
   return listing_markup(host,fresh,older),url,'html'
  with patch.object(p,'fetch',side_effect=fake_fetch):
   with patch.object(p,'BROWSER_ENABLED',False):
    _,_,st=p.scrape_one(src,DIMS,today-timedelta(days=29),{})
  self.assertFalse(st['coverage_complete'])
  self.assertEqual(st['detail_errors'],1)
  self.assertEqual(st['status'],'partial_coverage')
  cp=p.advance_checkpoint(p.make_checkpoint(src,{},today),st,today)
  self.assertFalse(cp['initialized'])

 def test_unrecognized_dates_cannot_finish_initialization(self):
  src=next(s for s in SOURCES if s['name']=='北京市发展和改革委员会')
  host=urlsplit(src['url']).hostname
  markup=f'<table><tr><td><a href="https://{host}/doc/new">关于风电机组并网技术标准的通知</a></td><td>发布日期：近期</td></tr></table>'
  with patch.object(p,'fetch',return_value=(markup,src['url'],'html')):
   with patch.object(p,'BROWSER_ENABLED',False):
    _,_,st=p.scrape_one(src,DIMS,date.today()-timedelta(days=29),{})
  self.assertFalse(st['coverage_complete'])
  self.assertIn(st['status'],('partial_coverage','needs_adapter'))

 def test_record_merge_unchanged_updated_and_legacy_preservation(self):
  src=SOURCES[0]
  old={'title':'关于风电技术规范的通知','source_id':src['id'],'url':'https://example.gov.cn/policy/1','source_excerpt':'第一条 风电设备必须合规','published_at':'2026-10-10','dimensions':['并网技术规范'],'importance':{'status':'人工核验评分','score':75,'priority':'P1'},'discovered_at':'2026-10-11T00:00:00Z','impact_design':'审查认证','impact_business':'核查资质'}
  rows,count=p.merge_legal_records([old],[dict(old)],{src['id']:src})
  self.assertEqual(count['unchanged'],1);self.assertEqual(len(rows),1)
  changed=dict(old,source_excerpt='第一条 风电设备必须更新合规文件',importance=p.importance(None,[]))
  rows,count=p.merge_legal_records([old],[changed],{src['id']:src})
  self.assertEqual(count['updated'],1)
  self.assertIsNone(rows[0]['importance']['priority'])
  self.assertEqual(rows[0]['prior_importance_pending_recheck']['priority'],'P1')
  self.assertEqual(rows[0]['originally_discovered_at'],old['discovered_at'])
  new=dict(old,url='https://example.gov.cn/policy/2')
  rows,count=p.merge_legal_records([old],[new],{src['id']:src})
  self.assertEqual(len(rows),2);self.assertEqual(count['inserted'],1)

 def test_wrapped_history_is_kept_and_not_deleted(self):
  src=SOURCES[0]
  old={'title':'早年风电法案','url':'https://example.gov.cn/policy/legacy','source_id':src['id'],
       'published_at':'2024-01-11','source_excerpt':'第一条 风电技术要求','dimensions':['并网技术规范']}
  records,changes=p.merge_legal_records({'policies':[old]},[],{src['id']:src})
  self.assertEqual(len(records),1)
  self.assertEqual(records[0]['title'],'早年风电法案')
  self.assertEqual(changes['inserted'],0)

 def test_pdf_attachment_discovery_on_same_official_domain_only(self):
  html='<a href="/assets/new-rule.pdf">下载法规原文PDF</a>' +\
       '<a href="https://untrusted.example/assets/rule.pdf">境外镜像</a>' +\
       '<a href="/download/rule.docx">附件</a>'
  pdfs=p.attachment_urls(html,'https://fgw.beijing.gov.cn/zc/list')
  self.assertEqual(pdfs,['https://fgw.beijing.gov.cn/assets/new-rule.pdf'])

 def test_prepare_does_not_visit_network_and_preserves_first_seen(self):
  with tempfile.TemporaryDirectory() as temp:
   cpfile=Path(temp)/'checkpoints.json'
   with patch.object(p,'CHECKPOINTS',cpfile):
    with patch.object(p,'fetch',side_effect=AssertionError('prepare must never fetch')):
     a=p.main(['--prepare'])
     first=cpfile.read_text()
     b=p.main(['--prepare'])
     second=cpfile.read_text()
   self.assertEqual(first,second)
   self.assertEqual(len(a),121)
   self.assertEqual(len(b),121)
   self.assertFalse(any(x['initialized'] for x in a.values()))

 def test_error_status_never_advances_checkpoint(self):
  cp=p.make_checkpoint(SOURCES[0],None,date.today())
  for status in ['blocked','connection_error','error','needs_adapter','partial_coverage']:
   st={'status':status,'coverage_complete':False,'message':'未完成核验'}
   cp=p.advance_checkpoint(cp,st,date.today())
   self.assertFalse(cp['initialized'])
   self.assertIsNone(cp['last_verified_date'])
  self.assertEqual(cp['total_attempts'],5)

 def test_main_two_sequential_daily_runs_and_failed_source_held_back(self):
  today=date.today()
  host_to_source={urlsplit(s['url']).hostname:s for s in SOURCES}
  # mock the per-station discovery path for all sites; older date proves 30-day boundary.
  fresh=(today-timedelta(days=2)).isoformat();older=(today-timedelta(days=45)).isoformat()
  failure_id=SOURCES[0]['id']
  def fake_scrape(src,dims,cutoff,manual):
   st={'id':src['id'],'name':src['name'],'status':'ok_no_recent','checked_at':p.clock(),
       'found_count':0,'candidate_links':2,'coverage_complete':True,'window_start':cutoff.isoformat(),
       'window_end':today.isoformat(),'message':'模拟完整目录','attempts':[],'adapter':src['id'],'adapter_strategy':p.load_adapter(src).strategy}
   if src['id']==failure_id:st.update(status='connection_error',coverage_complete=False,message='模拟网络故障')
   return [],[],st
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)
   paths={'DATA':path,'POLICIES':path/'policies.json','STATUSES':path/'status.json',
          'CANDIDATES':path/'review.json','STATE':path/'state.json','CHECKPOINTS':path/'checkpoints.json',
          'SCORES':path/'scores.json'}
   with ExitStack() as stack:
    for key,val in paths.items():stack.enter_context(patch.object(p,key,val))
    stack.enter_context(patch.object(p,'scrape_one',side_effect=fake_scrape))
    old_logging=p.LOG.disabled;p.LOG.disabled=True
    try:
     a=p.main([]);b=p.main([])
    finally:p.LOG.disabled=old_logging
   self.assertEqual(len(a),121);self.assertEqual(len(b),121)
   self.assertEqual(sum(x['phase']=='initial_month' for x in a),121)
   self.assertEqual(sum(x['phase']=='daily_increment' for x in b),120)
   self.assertEqual(sum(x['phase']=='initial_month' for x in b),1)
   ck=json.loads((path/'checkpoints.json').read_text())
   self.assertEqual(ck['sources'][failure_id]['consecutive_failures'],2)
   self.assertFalse(ck['sources'][failure_id]['initialized'])
   self.assertEqual(sum(v['initialized'] for v in ck['sources'].values()),120)
   self.assertEqual(len((path/'policy_site_acceptance.csv').read_text(encoding='utf-8-sig').splitlines()),122)
   self.assertEqual(json.loads((path/'state.json').read_text())['initialized_source_count'],120)

if __name__=='__main__':unittest.main()
