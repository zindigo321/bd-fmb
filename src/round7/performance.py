"""Measured cold pairing and conservative component forecasts."""
from pathlib import Path
import shutil
import numpy as np
from src.round2.evidence import read,write
from src.round3.experiments import compare


def memory_forecast(empty_stk_peak,b2_stk_peak,b2_python_peak):
    # The same executable's empty-scenario resident cost is not proportional
    # to propagated days. Credit only that measured baseline minus128MiB;
    # charge every remaining byte and the whole Python peak seven times.
    uncertainty=128*1024*1024
    fixed=max(0,min(empty_stk_peak,b2_stk_peak)-uncertainty)
    stk=fixed+7*(b2_stk_peak-fixed);python=7*b2_python_peak
    return dict(empty_scenario_measured_bytes=empty_stk_peak,b2_stk_measured_bytes=b2_stk_peak,b2_python_measured_bytes=b2_python_peak,
        fixed_baseline_credit_bytes=fixed,fixed_baseline_uncertainty_bytes=uncertainty,
        projected_stk_bytes=stk,projected_python_bytes=python,projected_combined_bytes=stk+python,
        memory_guard_factor=1.5,guarded_combined_bytes=1.5*(stk+python),
        formula='fixed=max(0,min(empty,B2_STK)-128MiB); STK7=fixed+7*(B2_STK-fixed); Python7=7*B2_Python; total margin=1.5')


def pair_check(out,cfg):
    out=Path(out);old=out/'benchmarks/cold_old';new=out/'benchmarks/cold_new'
    a=read(old/'experiment.json');b=read(new/'experiment.json');ar=read(old/'raw_intervals.json');br=read(new/'raw_intervals.json')
    matches=[]
    for r in ar:
        rr=[x for x in br if (x['sat_id'],x['point_id'])==(r['sat_id'],r['point_id'])]
        matches.append(compare([r],rr,cfg['round7']['cold_pair']['window'],cfg['tolerances']['convergence_s']))
    oa=read(old/'opportunities.json');ob=read(new/'opportunities.json');ta=read(oa[0]['trajectory']['path']);tb=read(ob[0]['trajectory']['path'])
    trajectory_equal=(ta['times_epsec']==tb['times_epsec'] and np.allclose(ta['positions_fixed_km'],tb['positions_fixed_km'],rtol=0,atol=1e-9)
        and np.allclose(ta['bases_screening'],tb['bases_screening'],rtol=0,atol=1e-12))
    lifea=read(old/'lifecycle.json');lifeb=read(new/'lifecycle.json')
    checks=dict(old_new_valid=a['status']==b['status']=='PASS',same_parameters=read(old/'parameters.json')==read(new/'parameters.json'),
        same_requested_and_actual_states=trajectory_equal,known_positive_retained=len(ar)>=1 and all(x['status']=='PASS' for x in matches),
        fresh_independent_instances=lifea['ownership']['pid']!=lifeb['ownership']['pid'] or lifea['ownership']['creation_time']!=lifeb['ownership']['creation_time'],
        same_window=a['window']==b['window']==cfg['round7']['cold_pair']['window'],cold_models=a['cold_cache'] is True and b['cold_cache'] is True,
        cleaned=lifea['status']==lifeb['status']=='PASS' and lifea['residual'] is lifeb['residual'] is False,
        dependency_closure=all(x['current_identical'] for x in a['old_dependency_closure']))
    result=dict(status='PASS' if all(checks.values()) else 'FAIL',checks=checks,order=['old','new'],cache='Fresh independent STK instance/model; identical setup readbacks warm only setup states',
        old=dict(supervisor_s=lifea['elapsed_s'],experiment=a,stk_peak_bytes=lifea['memory_peak_working_set_bytes']),
        new=dict(supervisor_s=lifeb['elapsed_s'],experiment=b,stk_peak_bytes=lifeb['memory_peak_working_set_bytes']),
        screening_ratio=a['screening_s']/b['screening_s'],end_to_end_ratio=lifea['elapsed_s']/lifeb['elapsed_s'],
        conclusion='Single predeclared paired measurement; no statistical speedup claim. Different candidate counts are allowed with full evidence.')
    write(out/'cold_pair_comparison.json',result);return result


def forecast(out,cfg,remaining_s):
    out=Path(out);b=read(out/'benchmarks/B2/experiment.json');life=read(out/'benchmarks/B2/lifecycle.json')
    prop=sum(m['propagation_commands_s'] for m in b['performance'].values());fixed=max(0.,b['propagation_setup_s']-prop)
    components=dict(fixed_28_object_setup_s=fixed,propagation_7x_s=7*prop,screening_7x_s=7*b['screening_s'],
        access_geometry_10x_s=10*b['access_and_geometry_s'],statistics_project_write_7x_s=7*b['statistics_and_project_write_s'],
        supervisor_overhead_s=max(30.,life['elapsed_s']-b['elapsed_s']),extra_candidate_serialization_reserve_s=60.)
    estimate=sum(components.values());guarded=1.5*estimate
    import win32api
    mem=win32api.GlobalMemoryStatusEx();disk=shutil.disk_usage(out)
    empty=read(out/'lifecycle/normal/lifecycle.json')
    memory=memory_forecast(empty['memory_peak_working_set_bytes'],life['memory_peak_working_set_bytes'],b['python_peak_working_set_bytes'])
    predicted_memory=memory['projected_combined_bytes']
    predicted_disk=1.2*7*b['output_bytes']
    checks=dict(main_cap=guarded<=10800,total_remaining=guarded+1200+300<=remaining_s,
        memory=memory['guarded_combined_bytes']<=mem['AvailPhys'],memory_baseline_valid=empty['status']=='PASS' and empty['residual'] is False,
        disk=predicted_disk<=disk.free,benchmark_complete=b['status']=='PASS' and b['window']==[0,86400] and b['satellites']==28)
    result=dict(status='PASS' if all(checks.values()) else 'FAIL',checks=checks,components=components,main_forecast_s=estimate,guarded_main_s=guarded,
        independent_reopen_reserved_s=1200,cleanup_reserved_s=300,cumulative_remaining_s=remaining_s,
        round6_linear_crosscheck_s=1561.690711*4,ratio_to_round6_linear_crosscheck=estimate/(1561.690711*4),
        memory_components=memory,predicted_peak_bytes=predicted_memory,available_memory_bytes=mem['AvailPhys'],predicted_output_bytes=predicted_disk,available_disk_bytes=disk.free,
        assumptions='Keep measured28-star construction; propagation and complete trajectory/screening ×7; Access/geometry ×10 for growth; final output/statistics ×7; extra60s; outer1.5 margin. Reopen separately1200s. No benchmark result enters main.')
    write(out/'performance_forecast.json',result);return result
