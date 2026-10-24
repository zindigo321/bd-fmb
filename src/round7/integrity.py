"""Independent source/window/query audit for the interval candidate schema."""
import math
from pathlib import Path
from src.round2.evidence import KEYS,read,write
from src.round6.integrity import audit_trajectories
from src.round4.frozen import EXPECTED


def audit_ledger(ctx,point_hash,opps,candidates,queries,raw,kind,half,duration):
    errors=[];ident={**{k:ctx[k] for k in KEYS},'point_set_sha256':point_hash,'sensor_id':'Camera'}
    expected={};all_sources=set();all_ids=set()
    for o in opps:
        if any(o.get(k)!=v for k,v in ident.items()):errors.append('OPPORTUNITY_IDENTITY')
        sources=o['candidates'];windows=[]
        for s in sources:
            sid=s.get('source_id')
            if not sid or sid in all_sources:errors.append('DUPLICATE_SOURCE')
            all_sources.add(sid)
            for a,b in s['possible_intervals']:
                if not all(math.isfinite(t) for t in [a,b]) or not s['coarse_bracket'][0]<=a<b<=s['coarse_bracket'][1]:errors.append('INVALID_POSSIBLE_INTERVAL')
                windows.append((max(0.,a-half),min(duration,b+half),sid))
        unions=[]
        for a,b,sid in sorted(windows):
            if unions and a<=unions[-1][1]:unions[-1][1]=max(b,unions[-1][1]);unions[-1][2].add(sid)
            else:unions.append([a,b,{sid}])
        actual=[c for c in candidates if (c['sat_id'],c['point_id'])==(o['sat_id'],o['point_id'])]
        if len(actual)!=len(unions):errors.append('CANDIDATE_LEDGER_MISMATCH')
        for u in unions:
            matches=[c for c in actual if c['local_query_window']==u[:2] and set(c['source_ids'])==u[2]]
            if len(matches)!=1:errors.append('CANDIDATE_SOURCE_WINDOW_MISMATCH');continue
            c=matches[0];cid=c['candidate_id'];expected[cid]=(o,c)
            if cid in all_ids:errors.append('DUPLICATE_CANDIDATE_ID')
            all_ids.add(cid)
            for s in sources:
                if (cid in s['candidate_ids']) != (s['source_id'] in u[2]):errors.append('SOURCE_REVERSE_BINDING')
        if o['refinement_candidates']!=len(unions):errors.append('OPPORTUNITY_COUNT')
    if len(expected)!=len(candidates):errors.append('CANDIDATE_LEDGER_MISMATCH')
    qmap={q.get('query_id'):q for q in queries}
    if len(qmap)!=len(queries):errors.append('DUPLICATE_QUERY_ID')
    bindings=[]
    for cid,(o,c) in expected.items():
        match=[q for q in queries if q.get('candidate_id')==cid]
        if len(match)!=1:errors.append(('MISSING_CANDIDATE_QUERY:' if not match else 'DUPLICATE_CANDIDATE_QUERY:')+cid);continue
        q=match[0];binding={**ident,'sat_id':o['sat_id'],'point_id':o['point_id'],'evaluation_window':o['evaluation_window']}
        if any(c.get(k)!=v or q.get(k)!=v for k,v in binding.items()):errors.append('CANDIDATE_QUERY_IDENTITY')
        if c.get('query_id')!=q.get('query_id') or q.get('query_id') not in o['queries'] or c.get('local_query_window')!=q.get('window'):errors.append('REVERSE_QUERY_BINDING')
        if c.get('query_status')!='COMPLETE' or q.get('query_status')!='COMPLETE' or q.get('query_type')!=kind:errors.append('QUERY_NOT_COMPLETE_OR_WRONG_KIND')
        original=q.get('unclipped_intervals',[]);clipped=[]
        for e in original:
            a,b=e['start_epsec'],e['stop_epsec']
            if not all(math.isfinite(t) for t in [a,b]) or not q['window'][0]<=a<b<=q['window'][1]:errors.append('INVALID_NATIVE_INTERVAL')
            lo=max(a,o['window'][0]);hi=min(b,o['window'][1])
            if lo<hi:clipped.append((lo,hi))
        if clipped!=[(e['start_epsec'],e['stop_epsec']) for e in q.get('intervals',[])]:errors.append('QUERY_CLIPPING')
        if q.get('raw_event_count')!=len(original) or c.get('event_count')!=len(clipped) or q.get('event_count')!=len(clipped):errors.append('QUERY_COUNT')
        bindings.append(dict(candidate_id=cid,query_id=q['query_id'],event_count=q.get('event_count'),source_ids=c['source_ids']))
    if any(q.get('candidate_id') not in expected for q in queries):errors.append('ORPHAN_QUERY')
    if sorted((q['query_id'],e['start_epsec'],e['stop_epsec']) for q in queries for e in q.get('intervals',[]))!=sorted((r['query_id'],r['start_epsec'],r['stop_epsec']) for r in raw):errors.append('QUERY_EVENT_SET_MISMATCH')
    if any(r.get('candidate_id')!=qmap.get(r.get('query_id'),{}).get('candidate_id') for r in raw):errors.append('EVENT_CANDIDATE_BINDING')
    return dict(status='FAIL' if errors else 'PASS',errors=errors,candidate_count=len(candidates),query_count=len(queries),empty_queries=sum(q.get('event_count')==0 for q in queries),bindings=bindings)


def integrity(ctx,cfg,folder,point_hash=EXPECTED,query_type='MAIN_FINE',trajectory_root=None):
    folder=Path(folder);opps=read(folder/'opportunities.json')
    ledger=audit_ledger(ctx,point_hash,opps,read(folder/'candidate_ledger.json'),read(folder/'raw_access_queries.json'),read(folder/'raw_intervals.json'),query_type,cfg['round3']['search']['candidate_half_window_s'],cfg['duration_s'])
    trajectory=audit_trajectories(opps,ctx,trajectory_root or folder,1e-8)
    for o in opps:
        pad=cfg['round3']['search']['padding_s'];w=o['evaluation_window']
        if o['screening_window']!=[max(0.,w[0]-pad),min(cfg['duration_s'],w[1]+pad)] or o['window']!=w or (o['coarse_step_s'],o['phase_s']) not in [(5.,0.),(2.,.371),(1.,.619)]:
            trajectory['status']='FAIL';trajectory['errors'].append('SAMPLING_CONTRACT')
    write(folder/'candidate_query_audit.json',ledger);write(folder/'trajectory_audit.json',trajectory)
    return dict(status='PASS' if ledger['status']==trajectory['status']=='PASS' else 'FAIL',ledger=ledger,trajectory=trajectory)
