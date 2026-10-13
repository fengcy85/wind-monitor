import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from policy_logic import analyze_document,score_verified_assessment,priority_from_score,unique_records,dimension_hits
from policy_crawler import links_from_html,allowed_link,parse_date,pdf_text,FetchProblem,scrape_source,links_from_feed,get_form_search_links
from policy_scrape import build_data,load_json

SRC=json.loads((ROOT/'policy_sources.json').read_text(encoding='utf-8'))[0]
TEST_SOURCE={**SRC,'id':'official-demo','name':'风电主管部门','country':'中国','region':'国内','url':'https://gov.example.org/notice','strategy':'html_pdf','max_articles':4,'max_links':9}
DATE='2026-10-13T04:00:00+00:00'


def classify(title,parts):
    return analyze_document(source=TEST_SOURCE,url='https://gov.example.org/notice/2026-01',title=title,body='\n\n'.join(parts),paragraphs=parts,published_at='2026-10-12',observed_at=DATE)


class RulesTest(unittest.TestCase):
    def test_dimensions_source_counts(self):
        sources=json.loads((ROOT/'policy_sources.json').read_text(encoding='utf-8'))
        dims=json.loads((ROOT/'regulatory_dimensions.json').read_text(encoding='utf-8'))
        self.assertEqual(len(sources),121)
        self.assertEqual(sum(s['region']=='国内' for s in sources),100)
        self.assertEqual(sum(s['region']=='海外' for s in sources),21)
        self.assertEqual(len({s['url'] for s in sources}),121)
        self.assertEqual(len(dims),18)
        self.assertTrue(all(not d['name'][0].isdigit() for d in dims))

    def test_direct_wind_grid_code(self):
        p='第十条 风电机组必须具备低电压穿越能力，风电设备制造商应按并网技术规范完成型式试验，提交测试报告并取得许可。'
        accepted,pending=classify('关于发布风电场并网技术规范的通知',[p])
        self.assertGreater(len(accepted),0)
        self.assertIn('并网技术规范',accepted[0]['dimensions'])
        self.assertIsNone(accepted[0]['importance_score'])
        self.assertIn('待',accepted[0]['priority'])
        self.assertIn('变流器',accepted[0]['product_impact'])
        self.assertIn('第十条',accepted[0]['clause_id'])
        self.assertEqual(p[:30],accepted[0]['evidence'][:30])

    def test_no_solar_noise(self):
        accepted,pending=classify('太阳能光伏项目规划新闻',['本市将开展太阳能光伏项目布局，预计明年举行论坛活动。']*3)
        self.assertEqual((accepted,pending),([],[]))

    def test_wind_not_sole_keyword_if_clauses_still_relevant(self):
        accepted,pending=classify('关于印发风电项目管理办法的公告',['第一条 所有风电项目供应商必须满足本地采购比例并提交原产地证明，符合条件才能参加风电项目招标。'])
        self.assertGreater(len(accepted),0)
        self.assertIn('本地化及原产地',accepted[0]['dimensions'])

    def test_generic_grid_requirement_goes_to_review_only(self):
        clause='Article 14 All power generating units connected to the grid shall provide frequency response and reactive power capability according to the grid code. Additional compliance reports must be submitted.'
        yes,review=classify('National Grid Code Technical Requirements',[clause])
        self.assertEqual(yes,[])
        self.assertTrue(review)
        self.assertIn('待核实',review[0]['relevance'])

    def test_non_binding_draft_observation(self):
        yes,review=classify('风电项目本地化政策征求意见稿',['第一条 风电项目投标人拟应符合本地采购比例要求，并提交原产地认证材料，不得提供虚假文件。'])
        self.assertEqual(yes,[])
        self.assertTrue(review)
        self.assertEqual(review[0]['document_type'],'政策观察')

    def test_empty_body_and_short_homepage_rejected(self):
        self.assertEqual(classify('风电强制监管公告',['导航菜单']),([],[]))
        self.assertEqual(classify('风电强制监管公告',[]),([],[]))

    def test_score_partial_and_verified(self):
        partial={'legal_verified':True,'scope_verified':True,'legal_status':'有效/待生效','official_evidence_url':'https://agency.example/doc',
          'D':3,'D_reason':'依据并网规定修改控制策略', 'B':4,'B_reason':'供应链须调整本地制造布局'}
        self.assertIsNone(score_verified_assessment(partial)['score'])
        full={**partial,'C':5,'A':4,'U':3,
              'C_reason':'不合规会造成禁售风险','A_reason':'多个目标产品适用该条款',
              'U_reason':'按最迟设计启动时间估算剩余四个月'}
        expected=6*3+5*4+4*5+3*4+2*3
        result=score_verified_assessment(full)
        self.assertEqual(result['score'],expected)
        self.assertEqual(result['priority'],priority_from_score(expected))
        self.assertEqual(result['forced_p0'],False)
        full.update({'blocking_verified':True,'business_affected_verified':True,'deadline_within_90_days_verified':True,'consequence_verified':True})
        result=score_verified_assessment(full)
        self.assertEqual(result['score'],expected)
        self.assertEqual(result['priority'],'P0 极高')
        self.assertTrue(result['forced_p0'])
        full['A']=None
        self.assertIsNone(score_verified_assessment(full)['score'])

    def test_priority_boundaries(self):
        for score,expected in [(0,'P4'),(29,'P4'),(30,'P3'),(49,'P3'),(50,'P2'),(69,'P2'),(70,'P1'),(84,'P1'),(85,'P0'),(100,'P0')]:
            self.assertTrue(priority_from_score(score).startswith(expected),(score,expected))

    def test_unique_records(self):
        a={'id':'same','review_rank':20,'published_at':None,'observed_at':'2026-10-11'}
        b={**a,'review_rank':40,'observed_at':'2026-10-13'}
        self.assertEqual(unique_records([a,b]),[b])


class CrawlerTest(unittest.TestCase):
    def test_links_and_wrong_domain_rejected(self):
        html='''<html><body><a href="/article/wind-grid-code-2026">New mandatory wind grid code technical requirements</a><a href="https://evil.org/steal">Mandatory wind code</a><a href="/tag/wind">All wind articles</a><a href="/document/standard.pdf">Product certification standard 61400 PDF</a></body></html>'''
        links,feeds=links_from_html(html.encode(),'https://gov.example.org/',TEST_SOURCE)
        self.assertEqual(len(links),2)
        self.assertTrue(all('evil.org' not in x[0] for x in links))
        self.assertFalse(allowed_link('file:///etc/passwd',TEST_SOURCE))
        self.assertFalse(allowed_link('javascript:alert(1)',TEST_SOURCE))

    def test_public_get_form_and_post_ignored(self):
        s={**TEST_SOURCE,'strategy':'search_portal'}
        html=b'<form method="GET" action="/search"><input name="q" type="search"><input type="hidden" name="lang" value="en"></form><form method="POST" action="/auth"><input name="keyword"></form>'
        urls=get_form_search_links(html,s['url'],s)
        self.assertEqual(len(urls),1)
        self.assertIn('q=wind',urls[0]);self.assertIn('lang=en',urls[0]);self.assertNotIn('auth',urls[0])

    def test_date_bad_and_feed(self):
        self.assertIsNone(parse_date('2026-02-30'))
        self.assertEqual(parse_date('2026年10月12日'),'2026-10-12')
        feed=b'<rss><channel><item><title>Wind regulation</title><link>https://gov.example.org/notice/r1</link></item></channel></rss>'
        self.assertEqual(len(links_from_feed(feed,'https://gov.example.org/',TEST_SOURCE)),1)

    def test_offline_source_success_with_mock_fetch(self):
        landing=b'''<html><head><title>Policy listing</title></head><body><nav>Main menu</nav><a href="/notice/r1">New 2026 wind grid code technical requirements</a></body></html>'''
        article='''<html><head><title>2026 Wind Turbine Grid Code Regulation</title><meta property="article:published_time" content="2026-10-01T12:00:00Z"></head><body><article><p>Article 4 All wind turbines must provide fault ride through capability under the grid code and comply with certification testing and technical requirements.</p><p>Article 5 Each wind power equipment manufacturer shall submit certification evidence for grid connection and meet reactive power requirements.</p></article></body></html>'''.encode()
        def fake_fetch(url,source,max_bytes=3000000):
            if url.endswith('/notice'):return landing,'text/html',url
            if url.endswith('/notice/r1'):return article,'text/html',url
            raise AssertionError(url)
        with mock.patch('policy_crawler.fetch',side_effect=fake_fetch):
            p,r,status=scrape_source(TEST_SOURCE,DATE)
        self.assertEqual(status['status'],'found')
        self.assertGreater(len(p),0)
        self.assertGreater(status['articles_checked'],1)
        self.assertTrue(p[0]['url'].endswith('/notice/r1'))

    def test_js_rendered_regulation_body_is_used(self):
        js_source={**TEST_SOURCE,'browser_fallback':True,'strategy':'browser_html_pdf'}
        shell=b'<html><title>Wind Policy Portal</title><body><div id="app"></div></body></html>'
        rendered=b'''<html><title>2026 Wind Turbine Grid Regulation</title><body><article><p>Article 4 All wind turbines must provide fault ride through and reactive power control as stipulated in this grid code. All wind turbine equipment manufacturers shall submit evidence of certification testing before connection.</p><p>Article 5 Grid operators must review wind power plant compliance reports and record the formal effective date for the connection approval.</p></article></body></html>'''
        with mock.patch('policy_crawler.fetch',return_value=(shell,'text/html',js_source['url'])), \
             mock.patch('policy_crawler.sitemap_links',return_value=[]), \
             mock.patch('policy_crawler.browser_render',return_value=rendered):
            accepted,review,status=scrape_source(js_source,DATE)
        self.assertTrue(accepted)
        self.assertEqual(status['status'],'found')
        self.assertIn('playwright',status['adapter_used'])

    def test_site_denial_is_reported(self):
        with mock.patch('policy_crawler.fetch',side_effect=FetchProblem('robots_denied','robots forbids it')):
            p,r,status=scrape_source(TEST_SOURCE,DATE)
        self.assertEqual((p,r),([],[]))
        self.assertEqual(status['status'],'robots_denied')
        self.assertFalse(status['http_success'])

    def test_data_kept_when_sources_offline(self):
        existing={'id':'ABC','url':'https://gov.example.org/doc','title':'法规模拟','source_id':TEST_SOURCE['id'],
           'importance_score':None,'review_rank':75,'published_at':'2026-10-01','observed_at':DATE}
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);(folder/'policies.json').write_text(json.dumps([existing],ensure_ascii=False))
            failed_status={'id':TEST_SOURCE['id'],'name':TEST_SOURCE['name'],'region':'国内','country':'中国','url':TEST_SOURCE['url'],
                'strategy':'html_pdf','status':'network_error','articles_checked':0,'links_discovered':0,'accepted':0,'pending':0,'http_success':False,'failure_details':[]}
            with mock.patch('policy_scrape.scrape_source',return_value=([],[],failed_status)):
                meta=build_data([TEST_SOURCE],folder,workers=1,run_id=DATE)
            self.assertEqual(meta['total_candidate_clauses'],1)
            self.assertEqual(load_json(folder/'policies.json',[])[0]['id'],'ABC')
            self.assertEqual(load_json(folder/'policy_source_status.json',[])[0]['status'],'network_error')


if __name__=='__main__':unittest.main()
