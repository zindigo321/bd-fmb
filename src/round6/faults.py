"""Controlled in-memory adaptation of historical evidence, never main data."""
import copy
from pathlib import Path
from src.round2.evidence import KEYS,read,write
from src.round4.frozen import EXPECTED
from .integrity import audit_ledger


def historical_empty_query_probe(ctx,cfg,output):
    prior=Path(cfg['round6']['source_round5']);opps=read(prior/'G/T5/opportunities.json')
    queries=read(prior/'G/T5/raw_access_queries.json');raw=read(prior/'G/T5/raw_intervals.json')
    identity={**{k:ctx[k] for k in KEYS},'point_set_sha256':EXPECTED,'sensor_id':'Camera'};candidates=[]
    for oi,o in enumerate(opps):
        o.update(identity,evaluation_window=o['window'])
        for ci,c in enumerate(o['candidates']):
            c['source_id']=f'controlled_source_{oi}_{ci}'
            if c['reason_code']!='REFINE_SENSOR_ACCESS':continue
            c['candidate_id']='controlled_'+c['query_id'];q=next(q for q in queries if q['query_id']==c['query_id'])
            q.update(identity,candidate_id=c['candidate_id'],query_type='CONTROLLED_HISTORICAL_ADAPTER',query_status='COMPLETE',
                candidate_root_epsec=c['exact_root_epsec'],raw_event_count=q['event_count'],unclipped_intervals=copy.deepcopy(q['intervals']))
            candidates.append(dict(identity,candidate_id=c['candidate_id'],query_id=c['query_id'],source_ids=[c['source_id']],
                sat_id=o['sat_id'],point_id=o['point_id'],candidate_root_epsec=c['exact_root_epsec'],coarse_bracket=c['coarse_bracket'],
                evaluation_window=o['window'],local_query_window=q['window'],event_count=q['event_count'],query_status='COMPLETE'))
    for r in raw:r.update(identity,candidate_id='controlled_'+r['query_id'])
    def check(qs):return audit_ledger(ctx,EXPECTED,opps,candidates,qs,raw,'CONTROLLED_HISTORICAL_ADAPTER',.05,172800.)
    baseline=check(queries);injections=[]
    for q in queries:
        if q['event_count']==0:
            result=check([x for x in queries if x['query_id']!=q['query_id']])
            expected='MISSING_CANDIDATE_QUERY:'+q['candidate_id']
            injections.append(dict(removed_query=q['query_id'],expected_error=expected,result=result,
                pass_expected_rejection=result['status']=='FAIL' and expected in result['errors']))
    answer=dict(status='PASS' if baseline['status']=='PASS' and len(injections)==2 and all(x['pass_expected_rejection'] for x in injections) else 'FAIL',
        kind='CONTROLLED_IN_MEMORY_SCHEMA_ADAPTATION_NOT_REAL_ROUND6_DATA',source_run_id=cfg['round6']['source_round5_run_id'],
        baseline=baseline,injections=injections,source_files_modified=False)
    write(Path(output)/'historical_empty_query_fault_probe.json',answer)
    return answer
