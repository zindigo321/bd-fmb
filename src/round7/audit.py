"""Explicit 28-star contract and independent expansion/history comparisons."""
import csv,re
from pathlib import Path
from src.round2.evidence import KEYS,read,write,canonical,digest
from src.round3.statistics import aggregate
from src.round4.frozen import bind as bind_old,EXPECTED
from src.round4.acceptance import manual_audit,evaluate
from src.round5.audit import validate_events
from .integrity import integrity


CAPS=dict(reliability_and_cold=1200,B1=600,B2=1500,main=10800,reopen=1200,lifecycle=240,cleanup_reserve=300,cumulative=18000)


def validate_config(cfg):
    r=cfg['round7'];c=cfg['constellation']
    if r['satellites']!=[f'Sat{i:03d}' for i in range(1,29)] or r['window']!=[0,604800] or cfg['duration_s']!=604800:raise ValueError('Round7 requires all original28 and complete7days')
    if (c['n'],c['planes'],c['phase_factor'],c['h_km'])!=(28,7,1,600):raise ValueError('Walker identity changed')
    if r['budget_caps_s']!=CAPS or r['candidate_guard_km']!=.05:raise ValueError('Frozen budget/candidate rule changed')
    if r['early_stop'] or r['chunking'] or r['phase_s']!=0 or cfg['round4']['coarse_step_s']!=5 or cfg['round3']['grid']['merge_tolerance_s']!=0:raise ValueError('Frozen scope/sampling changed')
    if cfg['round4']['tiers']!=[dict(id='T7',planes=7,duration_s=604800.,timeout_s=10800)]:raise ValueError('Round7 tier mismatch')
    if r['original_seven']!=r['satellites'][::4]:raise ValueError('Historical subset changed')
    for key,sats in [('B1',r['original_seven']),('B2',r['satellites'])]:
        if r['benchmarks'][key]!=dict(satellites=sats,window=[0,86400]):raise ValueError('Frozen benchmark changed')
    old=read(Path(r['source_round6'])/'config.json')['config']
    for k in ['constellation','earth','epoch_utcg','time_system','reference_frame','propagation_step_s','report_sample_step_s','diagnostic_surface','camera','access_tiers','tolerances']:
        if cfg[k]!=old[k]:raise ValueError('Physical baseline differs:'+k)
    if cfg['round3']['search']!=old['round3']['search']:raise ValueError('Frozen physical/refinement settings changed')
    return True


def bind(ctx,cfg):
    frozen,selection=bind_old(ctx,cfg)
    old=read(Path(cfg['round7']['source_round6'])/'selection.json')
    if old['run_id']!=cfg['round7']['source_round6_run_id'] or selection['baseline']!=old['baseline']:raise ValueError('Original28 baseline differs')
    selection['rule']='Round7 explicit all28 selection; round4/5/6 first-per-plane semantics unchanged'
    selection['tiers']=[dict(cfg['round4']['tiers'][0],selected=selection['baseline'])]
    if [x['sat_id'] for x in selection['tiers'][0]['selected']]!=cfg['round7']['satellites']:raise ValueError('Selection not all28')
    write(Path(ctx['output'])/'selection.json',selection);return frozen,selection


def compare_history(cfg,raw,points):
    prior=Path(cfg['round7']['source_round6']);old=read(prior/'G/T6/raw_intervals.json');tol=cfg['tolerances']['convergence_s']
    checks=[];matched=set();seven=[r for r in raw if r['sat_id'] in cfg['round7']['original_seven']]
    for e in old:
        rows=[r for r in seven if r['sat_id']==e['sat_id'] and r['point_id']==e['point_id'] and max(abs(r[k]-e[k]) for k in ['start_epsec','stop_epsec'])<=tol]
        checks.append(dict(old_record_id=e['record_id'],matched_ids=[r['record_id'] for r in rows],status='PASS' if len(rows)==1 else 'FAIL',
            endpoint_errors_s=[[r[k]-e[k] for k in ['start_epsec','stop_epsec']] for r in rows]))
        matched.update(r['record_id'] for r in rows)
    extra=[r for r in seven if r['record_id'] not in matched]
    return dict(status='PASS' if len(old)==12 and all(c['status']=='PASS' for c in checks) else 'FAIL',source=str(prior),
        source_run_id=cfg['round7']['source_round6_run_id'],history_used_as_search_seed=False,checks=checks,
        added_search_repair_events=extra,added_search_repair_events_require_current_geometry=True),seven


def compare_expansion(seven,allstats):
    rows=[]
    for a,b in zip(seven['points'],allstats['points']):
        contained=all(any(v['start_epsec']<=u['start_epsec'] and v['stop_epsec']>=u['stop_epsec'] for v in b['merged_intervals']) for u in a['merged_intervals'])
        duration=lambda p:sum(m['stop_epsec']-m['start_epsec'] for m in p['merged_intervals'])
        da,db=duration(a),duration(b)
        rows.append(dict(point_id=a['point_id'],seven_covered_s=da,all28_covered_s=db,seven_internal_gaps_s=a['internal_gaps_s'],all28_internal_gaps_s=b['internal_gaps_s'],
            status='PASS' if a['point_id']==b['point_id'] and contained and db+1e-10>=da else 'FAIL',subset_union_contained=contained))
    return dict(status='PASS' if len(rows)==16 and all(r['status']=='PASS' for r in rows) and allstats['covered_points']>=seven['covered_points'] else 'FAIL',points=rows,
        seven_total_uncovered_s=16*604800-sum(r['seven_covered_s'] for r in rows),all28_total_uncovered_s=16*604800-sum(r['all28_covered_s'] for r in rows),
        monotonic_claims='Only union inclusion, covered duration/count, uncovered duration. Gap count and conditional mean/max are not monotonic requirements.')


def audit_constraints(project,prior,satellites,point_ids):
    """Audit object bindings, not an assumed one-file-per-sensor layout.

    STK11 writes one Camera.sn shared by the Camera child of each satellite.
    Every satellite must explicitly name that child; all serialized constraint
    blocks and the exact satellite/facility inventory must still match.
    """
    project=Path(project);prior=Path(prior);checks=[];bindings=[]
    sats={p.stem:p for p in project.glob('*.sa')};points={p.stem:p for p in project.glob('*.f')}
    sensors={p.stem:p for p in project.glob('*.sn')}
    def blocks(path):return [re.sub(r'\s+',' ',b).strip() for b in re.findall(r'BEGIN AccessConstraints(.*?)END AccessConstraints',path.read_text(encoding='utf-8'),re.S)]
    def compare(path,source):
        a=blocks(source);b=blocks(path);checks.append(dict(path=str(path),reference=str(source),old=a,current=b,status='PASS' if a==b and bool(b) else 'FAIL'))
    inventory=set(sats)==set(satellites) and set(points)==set(point_ids)
    for sat in satellites:
        path=sats.get(sat)
        if path is None:bindings.append(dict(sat_id=sat,status='FAIL',reason='MISSING_SATELLITE_FILE'));continue
        text=path.read_text(encoding='utf-8');sub=re.search(r'BEGIN SubObjects(.*?)END SubObjects',text,re.S)
        groups=re.findall(r'Class Sensor\s+(.*?)END Class',sub.group(1),re.S) if sub else []
        names=[line.strip() for g in groups for line in g.splitlines() if line.strip()]
        good=names==['Camera'] and 'Camera' in sensors
        bindings.append(dict(sat_id=sat,satellite_path=str(path),sensor_children=names,sensor_file=str(sensors.get('Camera','')),status='PASS' if good else 'FAIL'))
        source=prior/(sat+'.sa')
        if not source.exists():source=prior/(satellites[0]+'.sa')
        compare(path,source)
    for pid,path in points.items():
        source=prior/path.name
        if not source.exists():checks.append(dict(path=str(path),status='FAIL',reason='NO_FROZEN_POINT_SOURCE'));continue
        compare(path,source)
    for name,path in sensors.items():
        source=prior/path.name
        if not source.exists():checks.append(dict(path=str(path),status='FAIL',reason='NO_FROZEN_SENSOR_SOURCE'));continue
        compare(path,source)
    expected_sensors={n for x in bindings for n in x.get('sensor_children',[])}
    inventory &= set(sensors)==expected_sensors=={'Camera'}
    ok=inventory and len(bindings)==len(satellites) and all(x['status']=='PASS' for x in checks+bindings)
    return dict(status='PASS' if ok else 'FAIL',inventory_matches=inventory,files=checks,satellite_sensor_bindings=bindings,
        satellite_files=len(sats),facility_files=len(points),unique_sensor_files=len(sensors),sensor_objects_bound=len(bindings),
        policy='Every satellite binds Camera; one shared serialized Camera.sn is valid. File count is not sensor object count.')


def audit_dataset(ctx,cfg):
    validate_config(cfg);out=Path(ctx['output']);folder=out/'G/T7';points=read(out/'grid/frozen.json')['points'];ids=[p['point_id'] for p in points]
    raw=read(folder/'raw_intervals.json');queries=read(folder/'raw_access_queries.json');geom=read(folder/'geometry_audit.json');opps=read(folder/'opportunities.json');stats=read(folder/'statistics.json');exp=read(folder/'experiment.json')
    valid=validate_events(ctx,cfg,points,raw,queries,geom,'round7');chain=integrity(ctx,cfg,folder)
    hand=manual_audit(ids,raw,stats,[0,604800]);history,sevenraw=compare_history(cfg,raw,points)
    seven=aggregate(ids,sevenraw,[0,604800],0);seven['metric_scope']='ROUND7_SAME_ALGORITHM_ORIGINAL_SEVEN_SUBSET'
    subset_hand=manual_audit(ids,sevenraw,seven,[0,604800]);expand=compare_expansion(seven,stats)
    checks=dict(events=valid['status']=='PASS',chain=chain['status']=='PASS',arithmetic=hand['status']==subset_hand['status']=='PASS',history=history['status']=='PASS',expansion=expand['status']=='PASS',
        complete=exp['window']==[0,604800] and exp['satellites']==28 and exp['points']==16,
        pairs=len(opps)==448 and {(o['sat_id'],o['point_id']) for o in opps}=={(s,p) for s in cfg['round7']['satellites'] for p in ids} and all(o['window']==[0,604800] and o['coarse_step_s']==5 and o['phase_s']==0 for o in opps),
        trajectories=len(chain['trajectory']['unique_files'])==28 and all(x['actual_count']==120961 for x in chain['trajectory']['unique_files']),
        no_early_access=read(folder/'screen_complete_before_access.json')['access_calls']==0,
        denominator=stats['denominator']==16 and stats['merge_tolerance_s']==0 and stats['covered_points']>=9,
        json_csv_query=exp['json_csv_query_consistency'])
    with (folder/'raw_intervals.csv').open(encoding='utf-8',newline='') as f:csvrows=list(csv.DictReader(f))
    checks['csv_records']=len(csvrows)==len(raw) and all(all(str(r[k])==c[k] for k in c if k in r) for r,c in zip(raw,csvrows))
    contributions=[];matrix=[]
    for sat in cfg['round7']['satellites']:
        rows=[r for r in raw if r['sat_id']==sat]
        with (folder/sat/'raw_intervals.csv').open(encoding='utf-8',newline='') as f:back=list(csv.DictReader(f))
        checks['star_'+sat]=read(folder/sat/'raw_intervals.json')==rows and back==[r for r in csvrows if r['sat_id']==sat]
        contributions.append(dict(sat_id=sat,event_count=len(rows),record_ids=[r['record_id'] for r in rows]));matrix.append(dict(sat_id=sat,**{pid:sum(r['point_id']==pid for r in rows) for pid in ids}))
    prior=Path(cfg['round7']['source_round6'])/'G/T6/project'
    constraints=audit_constraints(folder/'project',prior,cfg['round7']['satellites'],ids)
    checks['constraints']=constraints['status']=='PASS'
    gaps=[]
    for p in stats['points']:
        for a,b in zip(p['merged_intervals'],p['merged_intervals'][1:]):
            left=[r for r in raw if r['record_id'] in a['source_record_ids'] and r['stop_epsec']==a['stop_epsec']]
            right=[r for r in raw if r['record_id'] in b['source_record_ids'] and r['start_epsec']==b['start_epsec']]
            value=b['start_epsec']-a['stop_epsec'];bound=2*cfg['tolerances']['independent_event_s']
            gaps.append(dict(point_id=p['point_id'],previous_stop_epsec=a['stop_epsec'],next_start_epsec=b['start_epsec'],gap_s=value,
                subtraction=f"{b['start_epsec']!r} - {a['stop_epsec']!r} = {value!r}",previous_record_ids=[r['record_id'] for r in left],next_record_ids=[r['record_id'] for r in right],
                cross_satellite_revisit=any(x['sat_id']!=y['sat_id'] for x in left for y in right),numerically_stable=value>bound,endpoint_error_bound_s=bound,
                endpoint_evidence=[dict(record_id=r['record_id'],sat_id=r['sat_id'],query_id=r['query_id'],candidate_id=r['candidate_id'],geometry_path=str(folder/'geometry_audit.json'),reopen_path=str(out/'reopen/instance/access_comparison.json')) for r in left+right]))
    checks['gap_stability']=all(g['numerically_stable'] for g in gaps)
    phenomena=dict(same_point_multiple_satellites=[p for p in ids if len({r['sat_id'] for r in raw if r['point_id']==p})>=2],
        cross_satellite_same_point_revisit=[g for g in gaps if g['cross_satellite_revisit']],
        natural_cross_satellite_union=[dict(point_id=p['point_id'],interval=m) for p in stats['points'] for m in p['merged_intervals'] if len({r['sat_id'] for r in raw if r['record_id'] in m['source_record_ids']})>=2],
        natural_overlap_or_touch=[dict(point_id=p['point_id'],interval=m) for p in stats['points'] for m in p['merged_intervals'] if len(m['source_record_ids'])>1])
    for name,value in [('event_validation',valid),('independent_ledger',hand),('historical_seven_comparison',history),('same_run_seven_statistics',seven),('same_run_seven_independent',subset_hand),('same_run_expansion',expand),('satellite_contributions',contributions),('event_counts',matrix),('constraints_comparison',constraints),('internal_gaps',gaps),('observed_phenomena',{k:dict(observed=bool(v),evidence=v) for k,v in phenomena.items()})]:write(out/(name+'.json'),value)
    acceptance=evaluate(raw,stats,hand,geom,consistency=all(checks.values()),completed=checks['complete'] and checks['pairs'])
    result=dict(status='PASS' if all(checks.values()) else 'FAIL',checks=checks,acceptance=acceptance);write(out/'dataset_audit.json',result);return result
