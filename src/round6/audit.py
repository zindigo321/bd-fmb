"""Round6-specific contract; round5's 48-hour contract remains unchanged."""
from pathlib import Path
from src.round2.evidence import read,write
from src.round4.frozen import EXPECTED
from src.round5.audit import audit_dataset as audit_common
from .integrity import audit_ledger,audit_trajectories


def validate_config(cfg):
    r=cfg['round6'];c=cfg['constellation']
    sats=['Sat001','Sat005','Sat009','Sat013','Sat017','Sat021','Sat025']
    if cfg['duration_s']!=604800 or r['window']!=[0,604800] or r['satellites']!=sats:raise ValueError('Round6 requires original seven satellites and complete seven days')
    if (c['n'],c['planes'],c['phase_factor'],c['h_km'])!=(28,7,1,600):raise ValueError('Original Walker identity changed')
    if cfg['round4']['tiers']!=[dict(id='T6',planes=7,duration_s=604800.,timeout_s=r['main_timeout_s'])]:raise ValueError('Round6 requires exactly one formal tier')
    if r['early_stop'] or r['chunking'] or r['phase_s']!=0 or cfg['round4']['coarse_step_s']!=5 or cfg['round3']['grid']['merge_tolerance_s']!=0:raise ValueError('Frozen sampling/scope changed')
    if r['trajectory_time_tolerance_s']!=1e-8:raise ValueError('Trajectory tolerance changed')
    return True


def integrity(ctx,cfg,folder,point_hash=EXPECTED,query_type='MAIN_FINE',trajectory_root=None):
    folder=Path(folder);opps=read(folder/'opportunities.json')
    ledger=audit_ledger(ctx,point_hash,opps,read(folder/'candidate_ledger.json'),read(folder/'raw_access_queries.json'),
        read(folder/'raw_intervals.json'),query_type,cfg['round3']['search']['candidate_half_window_s'],cfg['duration_s'])
    trajectory=audit_trajectories(opps,ctx,trajectory_root or folder,cfg['round6']['trajectory_time_tolerance_s'])
    pad=cfg['round3']['search']['padding_s']
    for o in opps:
        expected_screen=[max(0.,o['evaluation_window'][0]-pad),min(cfg['duration_s'],o['evaluation_window'][1]+pad)]
        valid=o['screening_window']==expected_screen and o['window']==o['evaluation_window']
        valid &= (o['coarse_step_s'],o['phase_s']) in [(5.,0.),(2.,.371),(1.,.619)]
        if query_type=='MAIN_FINE':valid &= o['evaluation_window']==cfg['round6']['window'] and (o['coarse_step_s'],o['phase_s'])==(5.,0.)
        if not valid:
            trajectory['status']='FAIL';trajectory['errors'].append('SAMPLING_CONTRACT:'+o['sat_id']+'/'+o['point_id'])
    write(folder/'candidate_query_audit.json',ledger);write(folder/'trajectory_audit.json',trajectory)
    return dict(status='PASS' if ledger['status']==trajectory['status']=='PASS' else 'FAIL',ledger=ledger,trajectory=trajectory)


def audit_dataset(ctx,cfg):
    validate_config(cfg);out=Path(ctx['output']);folder=out/'G/T6'
    checks=integrity(ctx,cfg,folder)
    result=audit_common(ctx,cfg,'round6',dict(candidate_query=checks['ledger']['status']=='PASS',trajectory=checks['trajectory']['status']=='PASS'))
    raw=read(folder/'raw_intervals.json');gaps=read(out/'internal_gaps.json')
    for g in gaps:
        g.update(crosses_48h=g['previous_stop_epsec']<172800<g['next_start_epsec'],
            endpoint_evidence=[dict(record_id=r['record_id'],sat_id=r['sat_id'],query_id=r['query_id'],candidate_id=r['candidate_id'],
                geometry_path=str(folder/'geometry_audit.json'),reopen_path=str(out/'reopen/instance/access_comparison.json'))
                for r in raw if r['record_id'] in g['previous_record_ids']+g['next_record_ids']])
    write(out/'internal_gaps.json',gaps)
    phenomena=read(out/'observed_phenomena.json')
    evidence=[g for g in gaps if g['cross_satellite_revisit']]
    phenomena['cross_satellite_same_point_revisit']=dict(observed=bool(evidence),evidence=evidence)
    write(out/'observed_phenomena.json',phenomena)
    return result
