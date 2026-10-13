import importlib.util
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'policy_scrape.py'
SPEC = importlib.util.spec_from_file_location('policy_scrape', SCRIPT)
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)

SOURCE = dict(id='test_agency', name='北京市能源主管部门', region='中国', country='中国', province='北京市',
              url='https://fgw.beijing.gov.cn/policies/', crawl_mode='html_listing')
DIMS = [{'name': f'维度{i+1}'} for i in range(18)]
ARTICLE = '<html><head><meta name="PubDate" content="2026-10-12 08:30:00"></head><body><h1>关于推进海上风电并网建设的通知</h1><article>' + ('推进海上风电并网运行管理。' * 23) + '</article></body></html>'


class DateParsingTests(unittest.TestCase):
    def test_basic_date_formats_and_impossible_dates(self):
        for text in ('2026-10-12', '2026/10/12', '2026.10.12', '2026年10月12日', '20261012'):
            self.assertEqual(mod.parse_date(text), '2026-10-12', text)
        self.assertEqual(mod.parse_date('2026-02-30'), '')
        self.assertEqual(mod.parse_date('2029-01-01'), '')

    def test_date_precedence_separates_issue_effective_and_publication(self):
        html = '<html><head><meta name="Article_PublishDate" content="2026-10-11" /></head><body>' \
               '<section>成文日期：2026年10月01日 实施日期：2026-12-01 发布日期：2026年10月10日</section></body></html>'
        result = mod.extract_dates(html)
        self.assertEqual(result['published_at'], '2026-10-11')
        self.assertEqual(result['issued_at'], '2026-10-01')
        self.assertEqual(result['effective_at'], '2026-12-01')
        self.assertEqual(result['date_evidence'], '页面元数据')

    def test_jsonld_and_listing_fallback(self):
        html = '''<script type="application/ld+json">{"@context":"https://schema.org", "@type":"NewsArticle", "datePublished":"2026-10-09T09:30:00+08:00"}</script>'''
        self.assertEqual(mod.extract_dates(html)['published_at'], '2026-10-09')
        self.assertEqual(mod.extract_dates('<html><p>成文日期：2026-10-02</p></html>', '', '2026-10-07')['published_at'], '2026-10-07')

    def test_not_confuse_issued_date_for_published_at(self):
        result = mod.extract_dates('<html><body>成文日期：2026-10-07。实施日期：2026-10-09</body></html>')
        self.assertEqual(result['published_at'], '')
        self.assertEqual(result['issued_at'], '2026-10-07')

    def test_listing_link_date_uses_nearby_text(self):
        html = '''<ul><li><a href="/20261012/notice.html">海上风电并网管理通知</a><span>2026年10月12日</span></li></ul>'''
        self.assertEqual(mod.title_candidates(html, SOURCE['url'], SOURCE['url'])[0][2], '2026-10-12')

    def test_two_week_window_inclusive_and_daily(self):
        today = date(2026, 10, 13)
        self.assertTrue(mod.within_days('2026-09-30', 14, today))
        self.assertFalse(mod.within_days('2026-09-29', 14, today))
        self.assertTrue(mod.within_days('2026-10-13', 1, today))
        self.assertFalse(mod.within_days('2026-10-12', 1, today))


class ScraperTests(unittest.TestCase):
    def test_expired_policy_marked_not_as_valid(self):
        html = ARTICLE.replace('关于推进海上风电并网建设的通知', '（失效）关于推进海上风电并网建设的通知')
        obj, err = mod.create_policy('（失效）关于推进海上风电并网建设的通知', 'https://fgw.beijing.gov.cn/20261012.html', html, SOURCE, DIMS)
        self.assertFalse(err)
        self.assertIn('失效', obj['validity_status'])
        self.assertEqual(obj['published_at'], '2026-10-12')

    def test_repair_old_record_date_from_official_page(self):
        item = {'title':'海上风电并网管理通知', 'url':'https://fgw.beijing.gov.cn/repair.html',
                'published_at':'', 'source_excerpt':'发布日期未识别'}
        with patch.object(mod, 'get_page', return_value=(ARTICLE, item['url'], False)):
            repaired = mod.repair_legacy_date(item)
        self.assertEqual(repaired['published_at'], '2026-10-12')
        self.assertIn('重新核查', repaired['date_evidence'])
        self.assertEqual(item['published_at'], '')  # Existing input not silently mutated.

    def test_no_publication_date_goes_to_review(self):
        html = ARTICLE.replace('<meta name="PubDate" content="2026-10-12 08:30:00">', '')
        obj, reason = mod.create_policy('海上风电并网管理通知', 'https://fgw.beijing.gov.cn/x.html', html, SOURCE, DIMS)
        self.assertIsNone(obj)
        self.assertIn('发布日期', reason)

    def test_out_of_range_not_in_recent_policies(self):
        old = ARTICLE.replace('2026-10-12', '2024-10-12')
        listing = '<ul><li><a href="/old.html">海上风电并网建设通知</a></li></ul>'
        def fake_get(url):
            if url.endswith('/policies/'):
                return listing, url, False
            return old, url, False
        with patch.object(mod, 'get_page', side_effect=fake_get), patch.object(mod, 'ENABLE_BROWSER', False), patch.object(mod, 'china_today', return_value=date(2026,10,13)):
            items, review, status = mod.scrape_source(SOURCE, DIMS, 14)
        self.assertEqual(items, [])
        self.assertEqual(status['out_of_window_count'], 1)
        self.assertEqual(status['status'], 'ok_no_recent')

    def test_403_is_blocked_and_not_counterfeit_success(self):
        import requests
        response = requests.Response()
        response.status_code = 403
        response.url = SOURCE['url']
        err = requests.HTTPError('403 Forbidden', response=response)
        with patch.object(mod, 'get_page', side_effect=err):
            items, reviews, status = mod.scrape_source(SOURCE, DIMS, 14)
        self.assertEqual(items, [])
        self.assertEqual(status['status'], 'blocked')
        self.assertEqual(status['found_count'], 0)

    def test_dynamic_source_attempts_crawl_instead_of_skipping(self):
        src = dict(SOURCE, crawl_mode='special_adapter')
        html = '<ul><li><a href="/wind.html">海上风电并网管理通知</a><time datetime="2026-10-12"></time></li></ul>'
        with patch.object(mod, 'get_page', side_effect=[(html, src['url'], False),(ARTICLE, 'https://fgw.beijing.gov.cn/wind.html', False)]),\
             patch.object(mod, 'ENABLE_BROWSER', False), patch.object(mod, 'china_today', return_value=date(2026,10,13)):
            items, reviews, status = mod.scrape_source(src, DIMS, 14)
        self.assertEqual(len(items), 1)
        self.assertEqual(status['status'], 'ok_with_results')

    def test_first_run_backfill_then_daily_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / 'site' / 'data'
            data.mkdir(parents=True)
            (root / 'policy_sources.json').write_text(json.dumps([SOURCE], ensure_ascii=False), encoding='utf-8')
            (root / 'regulatory_dimensions.json').write_text(json.dumps(DIMS, ensure_ascii=False), encoding='utf-8')
            fake_policy = {'id':'p_example', 'url':'https://fgw.beijing.gov.cn/a.html', 'title':'风电并网规定',
                           'published_at':'2026-10-12', 'discovered_at':'2026-10-13T01:00:00Z',
                           'region':'中国','source_name':'测试来源'}
            days_observed = []
            def fake_scrape(source, dims, days):
                days_observed.append(days)
                return [dict(fake_policy)], [], {'id':source['id'], 'status':'ok_with_results', 'found_count':1}
            paths = {'ROOT':root, 'DATA':data, 'SOURCES':root/'policy_sources.json',
                     'DIMENSIONS':root/'regulatory_dimensions.json', 'POLICIES':data/'policies.json',
                     'REVIEW':data/'policy_candidates_review.json', 'STATUSES':data/'policy_source_status.json',
                     'STATE':data/'policy_crawl_state.json', 'CSV_FILE':data/'policies.csv'}
            with patch.multiple(mod, **paths), patch.object(mod, 'scrape_source', side_effect=fake_scrape):
                self.assertEqual(mod.main(['--days','1']),0)
                self.assertEqual(mod.main(['--days','2']),0)
            self.assertEqual(days_observed, [14,2])
            self.assertEqual(len(json.loads((data/'policies.json').read_text())),1)
            self.assertEqual(len(json.loads((data/'policy_sources.json').read_text())),1)
            self.assertTrue(json.loads((data/'policy_crawl_state.json').read_text())['backfill_completed'])


if __name__ == '__main__':
    unittest.main()
