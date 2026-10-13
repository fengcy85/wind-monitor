"""Run regulation crawler -> evidence-based clause candidates -> static site JSON.

Usage: python policy_scrape.py [--max-sources N] [--output-dir PATH]
Data is updated atomically and pre-existing results survive site outages.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
import json
import logging
import os
from pathlib import Path
import sys

from policy_crawler import scrape_source
from policy_logic import unique_records,apply_assessments

HERE=Path(__file__).resolve().parent
DATA_DIR=HERE/'site'/'data'


def load_json(path:Path,default):
    try:return json.loads(path.read_text(encoding='utf-8'))
    except (FileNotFoundError,json.JSONDecodeError,UnicodeDecodeError):return default


def write_json(path:Path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name('.'+path.name+'.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2,sort_keys=False)+'\n',encoding='utf-8')
    temp.replace(path)


def build_data(sources:list[dict],folder:Path,workers:int=5,run_id:str|None=None) -> dict:
    now=run_id or datetime.now(timezone.utc).isoformat(timespec='seconds')
    status=[];fresh=[];review=[]
    with ThreadPoolExecutor(max_workers=max(1,min(workers,8)),thread_name_prefix='policy') as executor:
        jobs={executor.submit(scrape_source,source,now):source for source in sources}
        for future in as_completed(jobs):
            src=jobs[future]
            try:
                approved,pending,item_status=future.result()
            except Exception as e:
                approved,pending=[],[]
                item_status={'id':src['id'],'name':src['name'],'country':src['country'],
                             'region':src['region'],'url':src['url'],'strategy':src['strategy'],
                             'checked_at':now,'status':'error','status_message':str(e)[:170],
                             'articles_checked':0,'links_discovered':0,'accepted':0,'pending':0,'http_success':False,
                             'adapter_used':[],'failure_details':[{'stage':'top','type':type(e).__name__,'detail':str(e)[:170]}]}
            status.append(item_status);fresh.extend(approved);review.extend(pending)
            logging.info('%s [%s] pages=%s found=%s review=%s',src['name'],item_status['status'],item_status.get('articles_checked',0),len(approved),len(pending))
    source_order={item['id']:i for i,item in enumerate(sources)}
    status.sort(key=lambda v:source_order.get(v['id'],1000000))

    old=load_json(folder/'policies.json',[])
    old_review=load_json(folder/'policy_review.json',[])
    old_by_id={x.get('id'):x for x in old if x.get('id')}
    merged=unique_records([*old,*fresh])
    manual=load_json(HERE/'policy_assessments.json',{})
    if not isinstance(manual,dict):manual={}
    merged=apply_assessments(merged,manual)
    recheck=unique_records([*old_review,*review])
    accepted_ids={x['id'] for x in merged}
    recheck=[x for x in recheck if x['id'] not in accepted_ids]
    # Do not accumulate identical or accidental scraper noise indefinitely.
    merged=merged[:3500];recheck=recheck[:1500]
    changed=[]
    old_lookup={(v.get('url'),v.get('clause_id')):v for v in old if v.get('url')}
    for v in fresh:
        o=old_lookup.get((v['url'],v['clause_id']))
        if not o:
            changed.append({'change_type':'new_clause_candidate','title':v['title'],'clause_id':v['clause_id'],'url':v['url'],'observed_at':now})
        elif o.get('evidence')!=v.get('evidence'):
            changed.append({'change_type':'possible_revision_needs_review','title':v['title'],'clause_id':v['clause_id'],'url':v['url'],'observed_at':now})
    changed=changed[:150]
    data_counts=Counter(s.get('status','not_run') for s in status)
    run={'last_run_at':now,'source_total':len(sources),'checked_source_count':len(status),
         'sources_with_http_response':sum(1 for s in status if s.get('http_success')),
         'sources_with_candidate_evidence':sum(1 for s in status if s['status']=='found'),
         'new_candidate_clauses':len({x['id'] for x in fresh}),
         'total_candidate_clauses':len(merged),'pending_review_clauses':len(recheck),
         'verified_and_scored_clauses':sum(x.get('importance_score') is not None for x in merged),
         'source_status_count':dict(data_counts),
         'warning':'站点访问成功 ≠ 完成法规检索。全部自动分析仅供初筛，效力及企业适用性须人工核验；检索平台可能需要专用官方接口。',
         'crawler_mode':'HTML / official RSS / text PDF / optional browser fallback; robots + retry + error diagnostics'}
    write_json(folder/'policies.json',merged)
    write_json(folder/'policy_review.json',recheck)
    write_json(folder/'policy_source_status.json',status)
    write_json(folder/'policy_meta.json',run)
    write_json(folder/'policy_changes.json',changed)
    return run


def main(argv:list[str]|None=None):
    parser=argparse.ArgumentParser(description='Conservative wind regulation monitor')
    parser.add_argument('--max-sources',type=int,default=0,help='Limited smoke test (0 means all)')
    parser.add_argument('--output-dir',type=Path,default=DATA_DIR)
    parser.add_argument('--workers',type=int,default=int(os.getenv('POLICY_MAX_WORKERS','5')))
    args=parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    sources=load_json(HERE/'policy_sources.json',[])
    if not sources:raise SystemExit('missing policy_sources.json')
    if args.max_sources:sources=sources[:args.max_sources]
    out=build_data(sources,args.output_dir,args.workers)
    print(json.dumps(out,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
