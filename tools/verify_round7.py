"""Standalone read-only numerical verification: no project scientific imports."""
import hashlib,json,math
from pathlib import Path


def verify(out):
    out=Path(out)
    def read(p):return json.loads(Path(p).read_text(encoding='utf-8'))
    summary=read(out/'summary.json');checks={};detail={}
    if not summary['formal_seven_days_completed']:
        return dict(status='BLOCKED',reason='No complete current28-star dataset',formal_started=summary['formal_experiment_started'])
    folder=out/'G/T7';raw=read(folder/'raw_intervals.json');stats=read(folder/'statistics.json');points=read(out/'grid/frozen.json')['points'];gaps=[];covered=0;rows=[]
    for p in points:
        events=sorted([r for r in raw if r['point_id']==p['point_id']],key=lambda r:(r['start_epsec'],r['stop_epsec']))
        sweep=sorted({r['start_epsec'] for r in events}|{r['stop_epsec'] for r in events});segments=[]
        for a,b in zip(sweep,sweep[1:]):
            ids=[r['record_id'] for r in events if r['start_epsec']<=a and r['stop_epsec']>=b]
            if ids:
                if segments and segments[-1][1]==a:segments[-1][1]=b
                else:segments.append([a,b])
        actual=next(x for x in stats['points'] if x['point_id']==p['point_id']);expected=[[m['start_epsec'],m['stop_epsec']] for m in actual['merged_intervals']]
        local=[b[0]-a[1] for a,b in zip(segments,segments[1:])];gaps+=local;covered+=bool(segments)
        rows.append(dict(point_id=p['point_id'],segments=segments,gaps=local,status='PASS' if segments==expected and local==actual['internal_gaps_s'] else 'FAIL'))
    checks['all16_endpoint_sweeps']=len(rows)==16 and all(x['status']=='PASS' for x in rows)
    checks['statistics']=covered==stats['covered_points'] and len(gaps)==stats['effective_gap_count'] and (sum(gaps)/len(gaps) if gaps else None)==stats['conditional_mean_gap_s'] and (max(gaps) if gaps else None)==stats['conditional_max_gap_s']
    queries=read(folder/'raw_access_queries.json');ledger=read(folder/'candidate_ledger.json');opps=read(folder/'opportunities.json')
    checks['candidate_queries']=len(ledger)==len(queries) and {c['candidate_id'] for c in ledger}=={q['candidate_id'] for q in queries} and len({q['query_id'] for q in queries})==len(queries) and all(q['query_status']=='COMPLETE' for q in queries)
    checks['query_events']=sorted((q['query_id'],e['start_epsec'],e['stop_epsec']) for q in queries for e in q['intervals'])==sorted((r['query_id'],r['start_epsec'],r['stop_epsec']) for r in raw)
    trajectory=[]
    for path in sorted({o['trajectory']['path'] for o in opps}):
        b=Path(path).read_bytes();d=json.loads(b);times=d['times_epsec'];wanted=[float(i*5) for i in range(120961)]
        refs=[o for o in opps if o['trajectory']['path']==path]
        ok=times==wanted and d['requested_times_epsec']==wanted and len(d['positions_fixed_km'])==len(d['bases_screening'])==120961 and len(refs)==16 and all(o['trajectory']['sha256']==hashlib.sha256(b).hexdigest() for o in refs)
        trajectory.append(dict(path=path,status='PASS' if ok else 'FAIL',count=len(times),references=len(refs)))
    checks['28_trajectories_448_references']=len(trajectory)==28 and len(opps)==448 and all(x['status']=='PASS' for x in trajectory)
    geometry=read(folder/'geometry_audit.json');gm={g['record_id']:g for g in geometry};reopened=read(out/'reopen/instance/access_comparison.json');rm={r['record_id']:r for r in reopened if not r.get('negative')};ev=[]
    for r in raw:
        g=gm[r['record_id']];rr=rm[r['record_id']];valid=True;diffs={}
        for name,values,tol in [('angular',g['angular_reference']['intervals'],.0002),('medium',g['medium_access']['intervals'],.00002),('reopen',rr['query']['intervals'],.00002)]:
            dif=[max(abs(max(v['start_epsec'],0)-r['start_epsec']),abs(min(v['stop_epsec'],604800)-r['stop_epsec'])) for v in values]
            diffs[name]=dif;valid &= len(dif)==1 and dif[0]<=tol
        # Independently use persisted exact states at each angular probe.
        states={v['time_epsec']:v for v in g['exact_states']};probe_errors=[]
        for p in g['angular_reference'].get('probes',[]):
            s=states[p['time_epsec']];delta=[g['target_fixed_km'][i]-s['position_fixed_km'][i] for i in range(3)];m=s['body_to_fixed']
            b=[sum(m[i][j]*delta[i] for i in range(3)) for j in range(3)]
            along=math.degrees(math.atan2(b[0],b[2]));cross=math.degrees(math.atan2(b[1],b[2]));margin=min(.001622-abs(along),.81-abs(cross)) if b[2]>0 else -1
            probe_errors.append(abs(margin-p['margin_deg']))
        valid &= bool(probe_errors) and max(probe_errors)<1e-9
        ev.append(dict(record_id=r['record_id'],status='PASS' if valid else 'FAIL',endpoint_differences=diffs,max_probe_margin_error_deg=max(probe_errors) if probe_errors else None))
    checks['all_events_geometry_reopen']=len(gm)==len(rm)==len(raw) and all(x['status']=='PASS' for x in ev)
    checks['all28_negative_queries']=sum(r.get('negative',False) for r in reopened)==28 and all(not r['query']['intervals'] for r in reopened if r.get('negative'))
    return dict(status='PASS' if all(checks.values()) else 'FAIL',checks=checks,points=rows,trajectories=trajectory,events=ev,event_count=len(raw),covered_points=covered,gap_count=len(gaps),
        conditional_mean_gap_s=sum(gaps)/len(gaps) if gaps else None,conditional_max_gap_s=max(gaps) if gaps else None,
        independence='Standalone standard library endpoint sweep/angle arithmetic/trajectory and reopen matching. Saved physical states still originate from STK.')


if __name__=='__main__':
    import sys
    result=verify(sys.argv[1]);print(json.dumps(result,ensure_ascii=False,indent=2));raise SystemExit(0 if result['status']=='PASS' else 1)
