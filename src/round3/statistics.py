"""Fixed-denominator engineering statistics, never official contest metrics."""
import math


def finite(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('Non-finite or non-numeric ' + name)
    return float(value)


def aggregate(point_ids, records, window, merge_tolerance_s=0.0, thresholds=(0.9, 1.0)):
    ids = list(point_ids)
    if not ids or len(ids) != len(set(ids)):
        raise ValueError('Point IDs must be a nonempty unique fixed denominator')
    left, right = [finite(x, 'window') for x in window]
    tolerance = finite(merge_tolerance_s, 'merge tolerance')
    if left >= right or tolerance < 0:
        raise ValueError('Invalid window/tolerance')
    grouped = {p: [] for p in ids}
    seen = set()
    excluded = []
    for item in records:
        if item['point_id'] not in grouped:
            raise ValueError('Unknown point would change fixed denominator')
        rid = item['record_id']
        if rid in seen:
            raise ValueError('Duplicate record ID; use distinct IDs for duplicate intervals')
        seen.add(rid)
        start = finite(item['start_epsec'], 'start')
        stop = finite(item['stop_epsec'], 'stop')
        if start >= stop:
            raise ValueError('Invalid interval ordering or zero length')
        st, sp = max(left, start), min(right, stop)
        if st >= sp:
            excluded.append(dict(record_id=rid, reason='OUTSIDE_WINDOW'))
            continue
        grouped[item['point_id']].append(dict(start_epsec=st, stop_epsec=sp,
            source_record_ids=[rid], clipped_start=start<left, clipped_stop=stop>right))
    points = []
    all_gaps = []
    firsts = []
    erased_gaps = []
    for pid in ids:
        intervals = sorted(grouped[pid], key=lambda x:(x['start_epsec'],x['stop_epsec']))
        merged = []
        for interval in intervals:
            if merged and interval['start_epsec'] <= merged[-1]['stop_epsec'] + tolerance:
                if interval['start_epsec'] > merged[-1]['stop_epsec']:
                    erased_gaps.append(dict(point_id=pid, gap_s=interval['start_epsec']-merged[-1]['stop_epsec']))
                merged[-1]['stop_epsec'] = max(merged[-1]['stop_epsec'],interval['stop_epsec'])
                merged[-1]['source_record_ids'] += interval['source_record_ids']
                merged[-1]['clipped_start'] |= interval['clipped_start']
                merged[-1]['clipped_stop'] |= interval['clipped_stop']
            else:
                merged.append(dict(interval))
        gaps = [b['start_epsec']-a['stop_epsec'] for a,b in zip(merged,merged[1:])]
        all_gaps.extend(gaps)
        if merged:
            firsts.append((merged[0]['start_epsec'],pid))
        points.append(dict(point_id=pid,raw_interval_count=len(intervals),merged_access_count=len(merged),
            merged_intervals=merged,internal_gaps_s=gaps,effective_gap_count=len(gaps),
            mean_internal_gap_s=sum(gaps)/len(gaps) if gaps else None,
            max_internal_gap_s=max(gaps) if gaps else None,
            no_gap_reason=None if gaps else 'FEWER_THAN_TWO_MERGED_ACCESSES',
            initial_gap_s=merged[0]['start_epsec']-left if merged else None,
            final_gap_s=right-merged[-1]['stop_epsec'] if merged else None,
            endpoint_gap_reason=None if merged else 'NO_OBSERVED_ACCESS',
            full_window_uncovered_s=right-left if not merged else None))
    cumulative = [dict(time_epsec=left,covered_points=0,coverage_fraction=0.0)]
    for i,(t,pid) in enumerate(sorted(firsts),1):
        if cumulative[-1]['time_epsec']==t:
            cumulative[-1]=dict(time_epsec=t,covered_points=i,coverage_fraction=i/len(ids))
        else:
            cumulative.append(dict(time_epsec=t,covered_points=i,coverage_fraction=i/len(ids)))
    threshold_results=[]
    for threshold in thresholds:
        threshold=finite(threshold,'coverage threshold')
        if not 0<threshold<=1:
            raise ValueError('Invalid coverage threshold')
        reached=next((row['time_epsec'] for row in cumulative if row['coverage_fraction']>=threshold),None)
        threshold_results.append(dict(fraction=threshold,elapsed_s=reached-left if reached is not None else None,
                                      reason=None if reached is not None else 'THRESHOLD_NOT_REACHED'))
    uncovered=sum(p['merged_access_count']==0 for p in points)
    insufficient=sum(p['merged_access_count']<2 for p in points)
    return dict(metric_scope='ROUND3_SMALL_SAMPLE_ENGINEERING_ONLY',window=[left,right],
        denominator=len(ids),covered_points=len(firsts),coverage_fraction=len(firsts)/len(ids),
        uncovered_points=uncovered,uncovered_fraction=uncovered/len(ids),
        insufficient_access_points=insufficient,insufficient_access_fraction=insufficient/len(ids),
        effective_gap_count=len(all_gaps),conditional_mean_gap_s=sum(all_gaps)/len(all_gaps) if all_gaps else None,
        conditional_max_gap_s=max(all_gaps) if all_gaps else None,
        gap_reason=None if all_gaps else 'NO_OBSERVED_INTERNAL_GAPS',
        conditional_statistic='Only observed internal gaps pooled by gap count; all insufficient points retained',
        merge_tolerance_s=tolerance,positive_gaps_erased_by_tolerance=erased_gaps,
        points=points,cumulative_coverage=cumulative,coverage_thresholds=threshold_results,
        excluded_records=excluded)
