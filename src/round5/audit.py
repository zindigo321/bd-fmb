"""Fail closed on event provenance, query, geometry or coverage mismatches."""
import csv
import math
from pathlib import Path
import re
from src.round2.evidence import read, write, KEYS
from src.round3.experiments import compare, csv_out
from src.round4.acceptance import manual_audit, evaluate
from src.round4.frozen import EXPECTED


def validate_config(cfg):
    r=cfg['round5']
    expected=['Sat001','Sat005','Sat009','Sat013','Sat017','Sat021','Sat025']
    if cfg['duration_s']!=172800 or r['window']!=[0,172800] or r['satellites']!=expected:
        raise ValueError('Round5 window/satellite identity changed')
    c=cfg['constellation']
    if (c['n'],c['planes'],c['phase_factor'],c['h_km'])!=(28,7,1,600):
        raise ValueError('Original Walker baseline changed')
    if cfg['round4']['tiers'] != [dict(id='T5',planes=7,duration_s=172800.,timeout_s=r['main_timeout_s'])]:
        raise ValueError('Round5 requires one complete declared task')
    if r['early_stop'] or r['chunking'] or cfg['round3']['grid']['merge_tolerance_s']!=0:
        raise ValueError('Early stopping, chunks or gap erasure are not enabled')
    return True


def validate_events(ctx,cfg,points,raw,queries,geometry,round_key='round5'):
    errors=[];events=[]
    ids=[p['point_id'] for p in points];sats=cfg[round_key]['satellites'];window=cfg[round_key]['window']
    seen=set();tuples=set();qmap={q.get('query_id'):q for q in queries}
    gmap={g.get('record_id'):g for g in geometry}
    if len(qmap)!=len(queries):errors.append('DUPLICATE_QUERY_ID')
    if len(gmap)!=len(geometry) or set(gmap)!={r.get('record_id') for r in raw}:errors.append('GEOMETRY_NOT_ONE_TO_ONE')
    expected_identity=dict(run_id=ctx['run_id'],config_sha256=ctx['config_sha256'],point_set_sha256=EXPECTED,sensor_id='Camera')
    def identity(obj):return all(obj.get(k)==v for k,v in expected_identity.items())
    for q in queries:
        if (not identity(q) or q.get('sat_id') not in sats or q.get('point_id') not in ids
            or q.get('evaluation_window')!=window or q.get('event_count')!=len(q.get('intervals',[]))):
            errors.append('INVALID_QUERY_IDENTITY_OR_COUNT:'+str(q.get('query_id')))
        rb=q.get('settings_readback',{})
        if any(rb.get(k)!=cfg['round3']['search'][k] for k in ['min_step_s','max_step_s','time_convergence_s']):
            errors.append('INVALID_FINE_QUERY_SETTINGS:'+str(q.get('query_id')))
        if rb.get('fixed_step') is not False or rb.get('precise_events') is not True or rb.get('light_time') is not False:
            errors.append('INVALID_ACCESS_FLAGS:'+str(q.get('query_id')))
    for r in raw:
        issues=[];rid=r.get('record_id');a=r.get('start_epsec');b=r.get('stop_epsec')
        if rid in seen or not isinstance(rid,str):issues.append('DUPLICATE_OR_INVALID_RECORD_ID')
        seen.add(rid)
        if not identity(r) or r.get('sat_id') not in sats or r.get('point_id') not in ids or r.get('window')!=window or r.get('event_status')!='REAL_SENSOR_ACCESS':issues.append('EVENT_IDENTITY')
        valid_numbers=all(isinstance(x,(float,int)) and not isinstance(x,bool) and math.isfinite(x) for x in [a,b,r.get('duration_s')])
        if not valid_numbers or not window[0]<=a<b<=window[1] or abs((b-a)-r['duration_s'])>1e-9:issues.append('EVENT_NUMBERS')
        key=(r.get('sat_id'),r.get('point_id'),a,b)
        if key in tuples:issues.append('DUPLICATE_SAME_SATELLITE_EVENT')
        tuples.add(key)
        q=qmap.get(r.get('query_id'),{});g=gmap.get(rid,{})
        if (q.get('sat_id')!=r.get('sat_id') or q.get('point_id')!=r.get('point_id') or q.get('window')!=r.get('local_refinement_window')
            or sum(e.get('start_epsec')==a and e.get('stop_epsec')==b for e in q.get('intervals',[]))!=1):issues.append('QUERY_BINDING')
        if (not identity(g) or g.get('sat_id')!=r.get('sat_id') or g.get('point_id')!=r.get('point_id')
            or g.get('event_endpoints')!=[a,b] or g.get('status')!='PASS' or g.get('local_endpoints_not_truncated') is not True
            or not g.get('exact_states')):issues.append('GEOMETRY_BINDING')
        if g.get('boundary_comparison',{}).get('status')!='PASS' or g.get('convergence',{}).get('status')!='PASS':issues.append('BOUNDARY_OR_CONVERGENCE')
        if g:
            # Re-match persisted values rather than trusting stored PASS strings.
            for label,ref,tol in [('angle',g.get('angular_reference',{}).get('intervals',[]),cfg['tolerances']['independent_event_s']),
                                  ('medium',g.get('medium_access',{}).get('intervals',[]),cfg['tolerances']['convergence_s'])]:
                if valid_numbers and compare(ref,[r],window,tol)['status']!='PASS':issues.append('RECOMPUTED_'+label.upper()+'_MISMATCH')
        events.append(dict(record_id=rid,status='FAIL' if issues else 'PASS',issues=issues,query_id=r.get('query_id')))
    qevents=sorted((q['query_id'],e['start_epsec'],e['stop_epsec']) for q in queries for e in q['intervals'])
    if qevents!=sorted((r['query_id'],r['start_epsec'],r['stop_epsec']) for r in raw):errors.append('QUERY_EVENT_SET_MISMATCH')
    return dict(status='PASS' if not errors and all(e['status']=='PASS' for e in events) else 'FAIL',errors=errors,events=events,
                invalid_events_not_filtered=True)


def history_comparison(cfg,raw,points,round_key='round5'):
    r=cfg[round_key];six=round_key=='round6'
    prior=Path(r['source_round5' if six else 'source_round4']);old=read(prior/('G/T5/raw_intervals.json' if six else 'G/T4/raw_intervals.json'))
    old_run=r['source_round5_run_id' if six else 'source_round4_run_id'];stop=172800. if six else 86400.
    if any(x['run_id']!=old_run for x in old):raise ValueError('Historical run identity mismatch')
    checks=[]
    for sat in r['satellites']:
        for p in points:
            pid=p['point_id']
            a=[r for r in old if r['sat_id']==sat and r['point_id']==pid]
            b=[dict(r,start_epsec=max(0.,r['start_epsec']),stop_epsec=min(stop,r['stop_epsec'])) for r in raw
               if r['sat_id']==sat and r['point_id']==pid and r['start_epsec']<stop and r['stop_epsec']>0]
            checks.append(dict(sat_id=sat,point_id=pid,comparison=compare(a,b,[0.,stop],cfg['tolerances']['convergence_s'])))
    return dict(status='PASS' if all(c['comparison']['status']=='PASS' for c in checks) else 'FAIL',
        source=str(prior),source_run_id=old_run,comparison_after_full_search=True,
        history_used_as_search_seed=False,checks=checks)


def audit_dataset(ctx,cfg,round_key='round5',extra_checks=None):
    six=round_key=='round6';r=cfg[round_key];stage='T6' if six else 'T5'
    out=Path(ctx['output']);folder=out/'G'/stage;points=read(out/'grid/frozen.json')['points']
    raw=read(folder/'raw_intervals.json');queries=read(folder/'raw_access_queries.json');geom=read(folder/'geometry_audit.json')
    stats=read(folder/'statistics.json');experiment=read(folder/'experiment.json');opps=read(folder/'opportunities.json')
    valid=validate_events(ctx,cfg,points,raw,queries,geom,round_key);write(out/'event_validation.json',valid)
    ids=[p['point_id'] for p in points];sats=r['satellites'];window=r['window']
    hand=manual_audit(ids,raw,stats,window);write(out/'independent_ledger.json',hand)
    history=history_comparison(cfg,raw,points,round_key);write(out/('first48h_comparison.json' if six else 'first24h_comparison.json'),history)
    checks=dict(events=valid['status']=='PASS',arithmetic=hand['status']=='PASS',history=history['status']=='PASS',
        complete=experiment['window']==window and experiment['satellites']==7 and experiment['points']==16,
        full_pairs=len(opps)==112 and {(o['sat_id'],o['point_id']) for o in opps}=={(s,p) for s in sats for p in ids}
            and all(o['window']==window for o in opps),
        no_early_access=read(folder/'screen_complete_before_access.json')['access_calls']==0,
        denominator=stats['denominator']==16 and stats['window']==window and stats['merge_tolerance_s']==0,
        json_csv_query=experiment['json_csv_query_consistency'])
    checks.update(extra_checks or {})
    with (folder/'raw_intervals.csv').open(encoding='utf-8',newline='') as f:csvrows=list(csv.DictReader(f))
    checks['csv_records']=len(csvrows)==len(raw) and all(all(str(r[k])==c[k] for k in c if k in r) for r,c in zip(raw,csvrows))
    matrix=[];contributions=[]
    for sat in sats:
        rows=[r for r in raw if r['sat_id']==sat]
        checks['json_'+sat]=read(folder/sat/'raw_intervals.json')==rows
        with (folder/sat/'raw_intervals.csv').open(encoding='utf-8',newline='') as f:back=list(csv.DictReader(f))
        checks['csv_'+sat]=back==[r for r in csvrows if r['sat_id']==sat]
        matrix.append(dict(sat_id=sat,**{pid:sum(r['point_id']==pid for r in rows) for pid in ids}))
        contributions.append(dict(sat_id=sat,event_count=len(rows),contributing_points=sorted({r['point_id'] for r in rows}),
            representative_event=rows[0] if rows else None,all_record_ids=[r['record_id'] for r in rows],
            evidence=[f'G/{stage}/{sat}/raw_intervals.json',f'G/{stage}/{sat}/raw_intervals.csv','event_validation.json','independent_ledger.json']))
    csv_out(out/'event_counts.csv',matrix,['sat_id',*ids]);write(out/'event_counts.json',matrix);write(out/'satellite_contributions.json',contributions)
    # Compare serialized constraint parameters, not merely counts or names.
    prior=Path(cfg[round_key]['source_round5' if six else 'source_round4'])/('G/T5/project' if six else 'G/T4/project');constraint_checks=[]
    for path in (folder/'project').rglob('*'):
        if path.suffix not in ['.sa','.sn','.f']:continue
        rel=path.relative_to(folder/'project');old=prior/rel
        def blocks(p):
            return [re.sub(r'\s+',' ',b).strip() for b in re.findall(r'BEGIN AccessConstraints(.*?)END AccessConstraints',p.read_text(encoding='utf-8'),re.S)]
        a=blocks(old);b=blocks(path)
        constraint_checks.append(dict(path=str(rel),old=a,current=b,status='PASS' if a==b and b else 'FAIL'))
    checks['constraints']=len(constraint_checks)>=24 and all(c['status']=='PASS' for c in constraint_checks)
    write(out/'constraints_comparison.json',constraint_checks)
    gaps=[]
    for p in stats['points']:
        for a,b in zip(p['merged_intervals'],p['merged_intervals'][1:]):
            left=[r for r in raw if r['record_id'] in a['source_record_ids'] and r['stop_epsec']==a['stop_epsec']]
            right=[r for r in raw if r['record_id'] in b['source_record_ids'] and r['start_epsec']==b['start_epsec']]
            value=b['start_epsec']-a['stop_epsec'];bound=2*cfg['tolerances']['independent_event_s']
            gaps.append(dict(point_id=p['point_id'],previous_stop_epsec=a['stop_epsec'],next_start_epsec=b['start_epsec'],gap_s=value,
                subtraction=f"{b['start_epsec']!r} - {a['stop_epsec']!r} = {value!r}",
                previous_record_ids=[r['record_id'] for r in left],next_record_ids=[r['record_id'] for r in right],
                cross_satellite_revisit=any(x['sat_id']!=y['sat_id'] for x in left for y in right),
                crosses_24h=a['stop_epsec']<86400<b['start_epsec'],endpoint_error_bound_s=bound,
                numerically_stable=value>bound))
    checks['gap_stability']=all(g['numerically_stable'] for g in gaps)
    write(out/'internal_gaps.json',gaps)
    phenomena={}
    phenomena['same_point_multiple_satellites']=[dict(point_id=p,record_ids=[r['record_id'] for r in raw if r['point_id']==p]) for p in ids if len({r['sat_id'] for r in raw if r['point_id']==p})>=2]
    phenomena['natural_cross_satellite_union']=[dict(point_id=p['point_id'],interval=m) for p in stats['points'] for m in p['merged_intervals'] if len({r['sat_id'] for r in raw if r['record_id'] in m['source_record_ids']})>=2]
    phenomena['natural_overlap_or_touch']=[dict(point_id=p['point_id'],interval=m) for p in stats['points'] for m in p['merged_intervals'] if len(m['source_record_ids'])>1]
    write(out/'observed_phenomena.json',{k:dict(observed=bool(v),evidence=v) for k,v in phenomena.items()})
    write(out/'deduplication.json',dict(chunking=False,exact_duplicate_events=0 if valid['status']=='PASS' else None,
        mapping=[dict(retained_record_id=r['record_id'],source_record_ids=[r['record_id']],query_id=r['query_id']) for r in raw],
        policy='Distinct satellites are never deduplicated. Duplicate same-satellite evidence causes failure; no silent removal.'))
    accept=evaluate(raw,stats,hand,geom,consistency=all(checks.values()),completed=checks['complete'] and checks['full_pairs'])
    result=dict(status='PASS' if all(checks.values()) else 'FAIL',checks=checks,acceptance=accept)
    write(out/'dataset_audit.json',result)
    return result
