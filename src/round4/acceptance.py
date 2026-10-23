"""Evidence-based H1/H2/H3, with a separate independent arithmetic audit."""
import math


def manual_audit(point_ids, raw, stats, window):
    """Independently recompute union endpoints with a sweep, not aggregate()."""
    seen=set();ledger=[];all_gaps=[];sources=[]
    for record in raw:
        if record['record_id'] in seen:raise ValueError('Repeated raw ID')
        seen.add(record['record_id'])
        if record['point_id'] not in point_ids:raise ValueError('Unknown point')
        a,b=record['start_epsec'],record['stop_epsec']
        if not all(math.isfinite(t) for t in (a,b)) or not window[0]<=a<b<=window[1]:raise ValueError('Invalid real interval')
    for pid,pstat in zip(point_ids,stats['points']):
        if pid!=pstat['point_id']:raise ValueError('Point order changed')
        records=sorted([r for r in raw if r['point_id']==pid],key=lambda r:(r['start_epsec'],r['stop_epsec']))
        endpoints=sorted([(r['start_epsec'],0,r['record_id']) for r in records]+[(r['stop_epsec'],1,r['record_id']) for r in records])
        depth=0;intervals=[];current_ids=[]
        for t,kind,rid in endpoints:
            if kind==0:
                if depth==0:start=t;current_ids=[]
                depth+=1;current_ids.append(rid)
            else:
                depth-=1
                if depth==0:intervals.append(dict(start_epsec=start,stop_epsec=t,source_record_ids=sorted(current_ids)))
        expected=[(x['start_epsec'],x['stop_epsec'],sorted(x['source_record_ids'])) for x in intervals]
        actual=[(x['start_epsec'],x['stop_epsec'],sorted(x['source_record_ids'])) for x in pstat['merged_intervals']]
        gaps=[intervals[i+1]['start_epsec']-intervals[i]['stop_epsec'] for i in range(len(intervals)-1)]
        ok=expected==actual and gaps==pstat['internal_gaps_s'] and len(records)==pstat['raw_interval_count']
        if intervals:
            ok &= pstat['initial_gap_s']==intervals[0]['start_epsec']-window[0]
            ok &= pstat['final_gap_s']==window[1]-intervals[-1]['stop_epsec']
        else:ok &= pstat['initial_gap_s'] is None and pstat['final_gap_s'] is None
        ok &= pstat['mean_internal_gap_s']==(sum(gaps)/len(gaps) if gaps else None)
        ok &= pstat['max_internal_gap_s']==(max(gaps) if gaps else None)
        ledger.append(dict(point_id=pid,status='PASS' if ok else 'FAIL',raw_sorted=records,manual_union=intervals,
            gaps=[dict(previous_stop_epsec=a['stop_epsec'],next_start_epsec=b['start_epsec'],gap_s=b['start_epsec']-a['stop_epsec']) for a,b in zip(intervals,intervals[1:])],
            initial_gap_s=pstat['initial_gap_s'],final_gap_s=pstat['final_gap_s']))
        all_gaps.extend(gaps);sources.extend(r for i in intervals for r in i['source_record_ids'])
    checks=dict(points=len(ledger)==len(point_ids)==stats['denominator'],
        point_values=all(x['status']=='PASS' for x in ledger),
        sources=sorted(sources)==sorted(seen) and len(sources)==len(seen),
        gap_count=len(all_gaps)==stats['effective_gap_count'],
        pooled_mean=(sum(all_gaps)/len(all_gaps) if all_gaps else None)==stats['conditional_mean_gap_s'],
        pooled_max=(max(all_gaps) if all_gaps else None)==stats['conditional_max_gap_s'],
        uncovered=sum(not x['manual_union'] for x in ledger)==stats['uncovered_points'],
        insufficient=sum(len(x['manual_union'])<2 for x in ledger)==stats['insufficient_access_points'])
    return dict(kind='REAL_GRID_INDEPENDENT_ENDPOINT_SWEEP',status='PASS' if all(checks.values()) else 'FAIL',checks=checks,
        all_points=ledger,pooled_gap_values_s=all_gaps,arithmetic_tolerance_s=1e-9)


def evaluate(raw,stats,handcheck,geometry,consistency=True,completed=True):
    contributions={s:sum(r['sat_id']==s for r in raw) for s in sorted({r['sat_id'] for r in raw})}
    valid=handcheck['status']=='PASS' and consistency
    geometry_ok=bool(geometry) and all(x['status']=='PASS' for x in geometry)
    def layer(name,goal,reason,validation=True):
        if not completed:status='SKIPPED';code='UPSTREAM_NOT_COMPLETED'
        elif not valid or not validation:status='FAIL';code='VALIDATION_FAILED'
        elif goal:status='PASS';code='REAL_EVIDENCE_VERIFIED'
        else:status='BLOCKED';code=reason
        return dict(id=name,status=status,required=True,reason_code=code,
                    reason={'H1':'真实固定网格正事件链路','H2':'同窗口真实多星输入汇总','H3':'真实内部空窗重访统计'}[name]+': '+code)
    return dict(H1=layer('H1',bool(raw),'NO_REAL_FIXED_GRID_EVENTS',geometry_ok if raw else True),
        H2=layer('H2',len(contributions)>=2,'FEWER_THAN_TWO_CONTRIBUTING_SATELLITES'),
        H3=layer('H3',stats['effective_gap_count']>0,'NO_OBSERVED_REAL_INTERNAL_GAPS'),
        contributing_satellites=contributions,
        same_point_multiple_satellites=any(len({r['sat_id'] for r in raw if r['point_id']==p['point_id']})>=2 for p in stats['points']),
        natural_cross_satellite_union=any(len({r['sat_id'] for r in raw if r['record_id'] in m['source_record_ids']})>=2 for p in stats['points'] for m in p['merged_intervals']),
        natural_overlap_or_touch=any(len(m['source_record_ids'])>1 for p in stats['points'] for m in p['merged_intervals']))
