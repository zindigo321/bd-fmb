"""Independent candidate/query and persisted time-array checks (no STK calls)."""
import math
from pathlib import Path
import numpy as np
from src.round2.evidence import KEYS, read, digest


def expected_times(window, step, phase, tolerance):
    lo,hi=window
    if not all(math.isfinite(v) for v in [lo,hi,step,phase]) or not lo<hi or step<=0 or not 0<=phase<step:
        raise ValueError('Invalid sampling declaration')
    count=max(0,math.floor((hi-lo-phase)/step)+1)
    interior=[lo+phase+k*step for k in range(count)]
    return np.array([lo]+[t for t in interior if lo+tolerance<t<hi-tolerance]+[hi])


def audit_trajectory(data, ctx, sat, window, step, phase, tolerance=1e-8):
    errors=[]
    if any(data.get(k)!=ctx[k] for k in KEYS) or data.get('sat_id')!=sat:errors.append('TRAJECTORY_IDENTITY')
    if data.get('window')!=window or data.get('step_s')!=step or data.get('phase_s')!=phase:errors.append('TRAJECTORY_DECLARATION')
    expected=expected_times(window,step,phase,tolerance)
    try:
        times=np.asarray(data['times_epsec'],dtype=float)
        pos=np.asarray(data['positions_fixed_km'],dtype=float)
        bases=np.asarray(data['bases_screening'],dtype=float)
        if times.ndim!=1 or len(times)!=len(expected):errors.append('TRAJECTORY_COUNT')
        if not np.all(np.isfinite(times)) or not np.all(np.diff(times)>0):errors.append('TRAJECTORY_ORDER_OR_NONFINITE')
        if times.shape!=expected.shape or not np.allclose(times,expected,atol=tolerance,rtol=0):errors.append('TRAJECTORY_GRID')
        if len(times)<2 or abs(times[0]-window[0])>tolerance or abs(times[-1]-window[1])>tolerance:errors.append('TRAJECTORY_ENDPOINTS')
        if len(times)>1 and np.max(np.diff(times))>step+tolerance:errors.append('TRAJECTORY_GAP')
        if pos.shape!=(len(times),3) or bases.shape!=(len(times),3,3):errors.append('TRAJECTORY_DIMENSIONS')
        if not np.all(np.isfinite(pos)) or not np.all(np.isfinite(bases)):errors.append('TRAJECTORY_STATE_NONFINITE')
    except (ValueError,TypeError,KeyError) as exc:
        errors.append('TRAJECTORY_ARRAYS:'+str(exc));times=[]
    return dict(status='FAIL' if errors else 'PASS',errors=errors,sat_id=sat,expected_count=len(expected),
        actual_count=len(times),window=window,step_s=step,phase_s=phase,tolerance_s=tolerance)


def audit_trajectories(opportunities,ctx,folder,tolerance=1e-8):
    audited={};references=[];errors=[];folder=Path(folder).resolve()
    for o in opportunities:
        ref=o.get('trajectory',{});path=Path(ref.get('path','')).resolve();key=str(path)
        if not path.is_relative_to(folder) or o['sat_id'] not in path.parts:
            errors.append('TRAJECTORY_PATH:'+key);continue
        declaration=(o['sat_id'],o['screening_window'],o['coarse_step_s'],o['phase_s'])
        if key not in audited:
            try:
                blob=path.read_bytes();actual_hash=digest(blob)
                import json
                value=audit_trajectory(json.loads(blob),ctx,*declaration,tolerance)
                value.update(path=key,sha256=actual_hash,declaration=declaration)
            except (OSError,ValueError,KeyError) as exc:value=dict(status='FAIL',errors=[repr(exc)],path=key)
            audited[key]=value
        v=audited[key]
        ok=v.get('sha256')==ref.get('sha256') and v.get('declaration')==declaration and v['status']=='PASS'
        if o.get('coarse_samples')!=v.get('actual_count'):ok=False
        references.append(dict(sat_id=o['sat_id'],point_id=o['point_id'],path=key,status='PASS' if ok else 'FAIL'))
    return dict(status='PASS' if not errors and all(r['status']=='PASS' for r in references) else 'FAIL',
        errors=errors,unique_files=list(audited.values()),references=references)


def audit_ledger(ctx,point_hash,opportunities,candidates,queries,raw,query_type,half_window,duration):
    errors=[];expected={};source_map={};source_ids=set()
    identity={**{k:ctx[k] for k in KEYS},'point_set_sha256':point_hash,'sensor_id':'Camera'}
    for o in opportunities:
        if any(o.get(k)!=v for k,v in identity.items()):errors.append('OPPORTUNITY_IDENTITY')
        for c in o['candidates']:
            sid=c.get('source_id')
            if not sid or sid in source_ids:errors.append('SOURCE_ID_DUPLICATE_OR_MISSING')
            source_ids.add(sid)
            if c['reason_code']=='REFINE_SENSOR_ACCESS':
                cid=c.get('candidate_id')
                if not cid or cid in expected:errors.append('CANDIDATE_ID_DUPLICATE_OR_MISSING')
                expected[cid]=(o,c);source_map[sid]=cid
        for c in o['candidates']:
            if c['reason_code']=='DUPLICATE_EXACT_ROOT':
                parent=o['candidates'][c['retained_coarse_index']]
                if abs(c['exact_root_epsec']-parent['exact_root_epsec'])>=.01:errors.append('INVALID_DEDUPLICATION')
                if parent['reason_code']=='REFINE_SENSOR_ACCESS':source_map[c['source_id']]=parent['candidate_id']
    cmap={c.get('candidate_id'):c for c in candidates};qmap={q.get('query_id'):q for q in queries}
    if len(cmap)!=len(candidates) or set(cmap)!=set(expected):errors.append('CANDIDATE_LEDGER_MISMATCH')
    if len(qmap)!=len(queries):errors.append('DUPLICATE_QUERY_ID')
    bindings=[]
    for cid,(o,source) in expected.items():
        c=cmap.get(cid,{})
        for k,v in {**identity,'sat_id':o['sat_id'],'point_id':o['point_id'],'evaluation_window':o['evaluation_window'],
                    'candidate_root_epsec':source['exact_root_epsec'],'coarse_bracket':source['coarse_bracket']}.items():
            if c.get(k)!=v:errors.append('CANDIDATE_BINDING:'+str(cid)+':'+k)
        expected_sources=sorted(s for s,x in source_map.items() if x==cid)
        if sorted(c.get('source_ids',[]))!=expected_sources:errors.append('CANDIDATE_SOURCE_MAP:'+str(cid))
        matches=[q for q in queries if q.get('candidate_id')==cid]
        if len(matches)!=1:
            errors.append('MISSING_CANDIDATE_QUERY:'+str(cid) if not matches else 'DUPLICATE_CANDIDATE_QUERY:'+str(cid));continue
        q=matches[0];root=source['exact_root_epsec']
        local=[max(0.,root-half_window),min(duration,root+half_window)]
        for k,v in {**identity,'sat_id':o['sat_id'],'point_id':o['point_id'],'evaluation_window':o['evaluation_window'],
            'window':local,'query_type':query_type,'query_status':'COMPLETE','candidate_root_epsec':root}.items():
            if q.get(k)!=v:errors.append('QUERY_BINDING:'+str(q.get('query_id'))+':'+k)
        if c.get('query_id')!=q.get('query_id') or source.get('query_id')!=q.get('query_id') or c.get('local_query_window')!=local:
            errors.append('REVERSE_QUERY_BINDING:'+str(cid))
        if c.get('query_status')!='COMPLETE':errors.append('CANDIDATE_QUERY_INCOMPLETE:'+str(cid))
        if q.get('event_count')!=len(q.get('intervals',[])) or c.get('event_count')!=q.get('event_count'):errors.append('QUERY_COUNT:'+str(cid))
        original=q.get('unclipped_intervals',[]);clipped=[];lo,hi=o['evaluation_window']
        for e in original:
            a,b=e['start_epsec'],e['stop_epsec']
            if not all(math.isfinite(x) for x in (a,b)) or not local[0]<=a<b<=local[1]:errors.append('RAW_QUERY_INTERVAL:'+str(cid));continue
            if max(a,lo)<min(b,hi):clipped.append((max(a,lo),min(b,hi)))
        if clipped!=[(e['start_epsec'],e['stop_epsec']) for e in q.get('intervals',[])]:errors.append('QUERY_CLIPPING:'+str(cid))
        if q.get('raw_event_count')!=len(original):errors.append('RAW_QUERY_COUNT:'+str(cid))
        bindings.append(dict(candidate_id=cid,query_id=q['query_id'],event_count=q.get('event_count'),source_ids=expected_sources))
    for q in queries:
        if q.get('candidate_id') not in expected:errors.append('ORPHAN_QUERY:'+str(q.get('query_id')))
    query_events=sorted((q['query_id'],e['start_epsec'],e['stop_epsec']) for q in queries for e in q.get('intervals',[]))
    if query_events!=sorted((r['query_id'],r['start_epsec'],r['stop_epsec']) for r in raw):errors.append('QUERY_EVENT_SET_MISMATCH')
    for r in raw:
        if r.get('candidate_id')!=qmap.get(r.get('query_id'),{}).get('candidate_id'):errors.append('EVENT_CANDIDATE_BINDING')
    return dict(status='FAIL' if errors else 'PASS',errors=errors,candidate_count=len(expected),query_count=len(queries),
        empty_queries=sum(q.get('event_count')==0 for q in queries),bindings=bindings)
