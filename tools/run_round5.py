"""Execute exactly one frozen 7 x 16 x 172800 s dataset and finalize evidence."""
import argparse
import datetime
from pathlib import Path
import os
import shutil
import subprocess
import sys
import time
import traceback
import uuid
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.round2.evidence import ROOT, KEYS, read, write, create_run, execute, validate_stage, files_manifest, code_manifest, canonical, digest
from src.round3.lifecycle import supervise, snapshot
from src.round4.frozen import bind
from src.round4.report import KNOWN_SENTENCE, save_report
from src.round5.audit import validate_config, audit_dataset
from tools.run_round3 import Tee

STAGES=['data','environment','generate','tests','frozen','lifecycle','main','search_regression','reopen','dataset']


def run(ctx,cfg,state):
    out=Path(ctx['output']);r=cfg['round5']
    for name in ['data','environment','generate','tests']:
        cmd=execute(ctx,name,[sys.executable,'-X','utf8','-m','src.round2.worker','--output',str(out),'--stage',name],120)
        check=validate_stage(ctx,name,cmd);state[name]=check
        print('BASE',name,check['status'],round(cmd['elapsed_s'],3),flush=True)
    frozen,selection=bind(ctx,cfg)
    selection['rule']='First satellite of each of the seven original Walker-Delta planes; all 28 identities preserved'
    write(out/'selection.json',selection)
    historical=read(Path(r['source_round4'])/'selection.json')
    if selection['baseline']!=historical['baseline'] or selection['baseline_count']!=28:
        raise ValueError('Original 28 satellite identity changed')
    previous=read(Path(r['source_round4'])/'config.json')['config']
    fixed=['constellation','earth','epoch_utcg','time_system','reference_frame','propagation_step_s','report_sample_step_s',
           'diagnostic_surface','camera','access_tiers','tolerances']
    if any(cfg[k]!=previous[k] for k in fixed) or cfg['round3']['search']!=previous['round3']['search']:
        raise ValueError('Frozen physical or search settings changed')
    state['frozen']=dict(status='PASS',point_set_sha256=frozen['point_set_sha256'],physical_fields_compared=fixed)
    write(out/'preconditions.json',state)
    if any(state[s]['status']!='PASS' for s in ['data','environment','generate','tests']):return
    if read(out/'environment/environment.json')['stk']['status']!='PASS':
        state['main']=dict(status='BLOCKED',reason_code='STK_DEPENDENCY_UNAVAILABLE');return
    start=time.perf_counter();deadline=start+r['total_stk_budget_s']
    def task(name,folder,payload,cap):
        remaining=deadline-time.perf_counter()-r['cleanup_reserve_s']
        if remaining<30:
            value=dict(status='BLOCKED',reason_code='PREDECLARED_TOTAL_BUDGET_EXHAUSTED')
        else:value=supervise(ctx,cfg,name,folder,payload,timeout_s=min(cap,remaining))
        write(out/'stage_progress.json',state)
        return value
    lifes=[];lstart=time.perf_counter()
    for name in ['normal','exception','timeout']:
        remaining=r['lifecycle_budget_s']-(time.perf_counter()-lstart)
        if remaining<30:
            lifes.append(dict(status='BLOCKED',reason_code='LIFECYCLE_BUDGET_EXHAUSTED'));break
        lifes.append(task(name,out/'lifecycle'/name,None,min(120,remaining)))
    state['lifecycle']=dict(status='PASS' if len(lifes)==3 and all(x['status']=='PASS' for x in lifes) else 'FAIL',tasks=lifes)
    if state['lifecycle']['status']!='PASS':return
    tier=selection['tiers'][0]
    write(out/'effective_config.json',dict(**{k:ctx[k] for k in KEYS},tier=tier,config=cfg,effective_config_sha256=digest(canonical(cfg))))
    state['main']=task('r4_T5',out/'G/T5',dict(tier=tier,frozen=str(out/'grid/frozen.json')),r['main_timeout_s'])
    # Independent diagnostics never feed their constructed points or known times into main.
    state['search_regression']=task('cases',out/'search_regression/cases',None,r['regression_timeout_s'])
    if state['search_regression']['status']=='PASS':
        exp=read(out/'search_regression/cases/experiment.json')
        if exp.get('cases')!=16 or exp.get('searches')!=48 or exp['status']!='PASS':state['search_regression']['status']='FAIL'
    if state['main']['status']=='PASS':
        state['reopen']=task('r4_reopen',out/'reopen/instance',dict(source=str(out/'G/T5'),tier=tier,frozen=str(out/'grid/frozen.json')),r['reopen_timeout_s'])
        state['dataset']=audit_dataset(ctx,cfg)
    state['real_stk_elapsed_s']=time.perf_counter()-start
    write(out/'stage_progress.json',state)


def finalize(ctx,cfg,state,started):
    out=Path(ctx['output']);preservation=[]
    for f in read(out/'input_manifest.json')['files']:
        try:ok=digest(Path(f['path']).read_bytes())==f['sha256'];error=None
        except OSError as exc:ok=False;error=repr(exc)
        preservation.append(dict(path=f['path'],unchanged=ok,error=error))
    inputs_ok=all(p['unchanged'] for p in preservation)
    code_ok=code_manifest()==read(out/'code_manifest.json')['files']
    write(out/'preservation_check.json',dict(status='PASS' if inputs_ok and code_ok else 'FAIL',inputs=preservation,code_unchanged=code_ok))
    life=[]
    for p in out.rglob('lifecycle.json'):
        x=read(p);life.append(dict(path=str(p),**x))
    write(out/'lifecycle_index.json',life)
    owned={x['ownership']['pid'] for x in life if x.get('ownership')};after=snapshot()
    residual=[p for p in after if p['pid'] in owned]
    cleanup=bool(life) and all(x.get('residual') is False for x in life) and not residual
    write(out/'cleanup.json',dict(status='PASS' if cleanup else 'FAIL',after=after,owned_residual=residual,
        unrelated_instances_untouched=True,limitation='External forced termination of the top-level supervisor is not tested'))
    for stage in STAGES:state.setdefault(stage,dict(status='BLOCKED',reason_code='UPSTREAM_NOT_COMPLETED'))
    common=all(state[s]['status']=='PASS' for s in STAGES) and inputs_ok and code_ok and cleanup
    accept=state['dataset'].get('acceptance',{})
    for key in ['H1','H2','H3']:
        goal=accept.get(key,dict(id=key,status='BLOCKED',reason_code='NO_COMPLETE_CURRENT_DATASET'))
        if not common:
            goal=dict(goal,status='FAIL' if state['dataset']['status']=='FAIL' or not inputs_ok or not code_ok else 'BLOCKED',
                      reason_code='COMMON_EVIDENCE_PREREQUISITE_FAILED',prior_dataset_result=goal)
        accept[key]=goal
    overall='PASS' if common and all(accept[k]['status']=='PASS' for k in ['H1','H2','H3']) else 'FAIL'
    stats=read(out/'G/T5/statistics.json') if (out/'G/T5/statistics.json').exists() else None
    counts={s:None for s in cfg['round5']['satellites']}
    if (out/'satellite_contributions.json').exists():counts={r['sat_id']:r['event_count'] for r in read(out/'satellite_contributions.json')}
    summary=dict(**{k:ctx[k] for k in KEYS},core_acceptance=overall,exit_code=0 if overall=='PASS' else 1,
        acceptance=accept,stages=state,per_satellite_event_counts=counts,statistics=stats,
        evidence_valid=common,inputs_preserved=inputs_ok,code_unchanged=code_ok,cleanup_passed=cleanup,
        elapsed_s=time.perf_counter()-started,real_stk_elapsed_s=state.get('real_stk_elapsed_s'),
        data_quality=dict(original_AT_description='FAIL',required=False,source_unchanged=inputs_ok))
    write(out/'summary.json',summary)
    h='/'.join(accept[k]['status'] for k in ['H1','H2','H3'])
    text='# 第五轮七星、同一16点、48小时审计\n\n'+KNOWN_SENTENCE+'\n\n'
    text+=f"运行 `{ctx['run_id']}`，整体 **{overall}**，命令退出码 **{summary['exit_code']}**。H1/H2/H3：{h}。\n\n"
    text+='正式数据只来自本轮单一完整窗口 EpSec [0,172800]，历元为暂定 UTCG 2027-01-01 00:00:00 至 2027-01-03 00:00:00。未运行第四轮逐档循环，无提前成功停止、无跨轮拼接、无历史候选种子。\n\n'
    text+='|检查|状态|\n|---|---|\n'+''.join(f"|{s}|{state[s]['status']}|\n" for s in STAGES)+'\n'
    for key in ['H1','H2','H3']:text+=f"- {key}：{accept[key]['status']} / {accept[key]['reason_code']}。\n"
    text+='\n|卫星|真实事件数|\n|---|---:|\n'+''.join(f'|{s}|{n}|\n' for s,n in counts.items())+'\n'
    if stats:
        text+=f"贡献星 {sum(n is not None and n>0 for n in counts.values())} 颗；覆盖 {stats['covered_points']}/16，未覆盖 {stats['uncovered_points']}/16（{stats['uncovered_fraction']:.2%}）；少于两次合并访问 {stats['insufficient_access_points']}/16（{stats['insufficient_access_fraction']:.2%}）。内部空窗 {stats['effective_gap_count']} 个，条件均值 {stats['conditional_mean_gap_s']} 秒，条件最大值 {stats['conditional_max_gap_s']} 秒。JSON无值保持null；首尾边缘空窗单列，不计入条件统计。\n\n"
    text+='112组机会在G/T5/opportunities.json；查询与逐星JSON/CSV、几何和中细收敛、轨迹及坐标变换状态在G/T5。每条事件一对一绑定run_id、配置、点集、卫星、点、查询和几何；无效事件显式失败，不过滤求PASS。合并容差0；独立端点扫描与逐条减法见independent_ledger.json及internal_gaps.json。事件矩阵、贡献表和自然现象分别见event_counts.csv、satellite_contributions.json、observed_phenomena.json。\n\n'
    text+=f"前24小时结果在本轮完整搜索后与第四轮T4按星点逐组匹配，见first24h_comparison.json。第四轮仅Sat021命中G_0306_1186，区间[53566.003944288,53566.008806503]；第四轮H1 PASS、H2/H3 BLOCKED、整体FAIL、退出码1且未超时的事实保留。next_scale.json为七星32点24小时估算，审计摘要及后来next_step_review.json为原16点48小时；本轮独立预算估算为 {cfg['round5']['budget']['estimated_s']:.4f} 秒，主任务1200秒、诊断420秒、生命周期240秒、重开300秒、总预算2400秒，见budget_decision.json。估算不是实测保证。\n\n"
    text+='重开在另一个本轮新建实例中加载保存工程，逐星核对根数、场景及传播区间、相机、姿态和16点，复算全部正事件并执行七个固定点短负查询。负查询仅覆盖各星首点[0,0.1]秒。生命周期证据区分正常退出、受控异常和预设超时强制回收；只用握手确认并复制持有的确切句柄回收自有实例，未知实例不操作。\n\n'
    text+=f"输入保全：{inputs_ok}；运行中代码未变：{code_ok}；本轮实例清理：{cleanup}。实际命令见invocation.json与reproduction.md；日志目录 `{ctx['work']}`。最终耗时及实际进程返回码另见command_result.json。原始AT中文描述损坏仍为非阻塞数据质量FAIL，未修改原文。\n\n"
    text+='这是七星、16个固定调试点、48小时内搜索观测到的工程条件统计。16点均在主体区域，两个岛屿无子集点；不能代表全区域。5秒筛查无变号擦边、格内多根、仅边缘相交等能力没有全局完备证明；16组48次诊断也不证明全局无漏检。角度方法独立于Access边界求解，但共享STK底层轨迹和坐标变换，不是独立物理真值。UTCG、地球常数、视场半角仍待主办方确认。未运行构型优化、3264点或7天评价，未输出官方指标或评分。外部强杀顶层监督器的回收能力未验证。\n'
    report=save_report(out,summary,text)
    final=dict(**{k:ctx[k] for k in KEYS},command=dict(started=True,completed=True,exit_code=summary['exit_code'],
        timed_out=any(x.get('timed_out') for x in life if x.get('task')!='timeout'),elapsed_s=time.perf_counter()-started),
        evidence=dict(status='PASS' if common else 'FAIL',current_run=True,inputs_preserved=inputs_ok,code_unchanged=code_ok),
        business=dict(core_acceptance=overall,**{k:accept[k] for k in ['H1','H2','H3']}),
        reporting=dict(generated=report['report_generated'],encoding=report['report_encoding']['status']),
        cleanup=dict(status='PASS' if cleanup else 'FAIL',owned_residual=residual))
    write(out/'final_status.json',final)
    index={
        'summary':['audit_report.md','summary.json','final_status.json','checklist.md'],
        'baseline':['baseline.json','history_review.json','budget_decision.json','reproduction.md','preconditions.json'],
        'frozen':['config.json','effective_config.json','grid/frozen.json','grid/fixed_grid.csv','selection.json','code_snapshot.zip','input_manifest.json','code_manifest.json'],
        'events':['G/T5/opportunities.json','G/T5/raw_access_queries.json','G/T5/raw_intervals.json','G/T5/raw_intervals.csv','event_counts.csv','satellite_contributions.json'],
        'aggregation':['G/T5/statistics.json','G/T5/merged_intervals.json','G/T5/provenance.json','independent_ledger.json','internal_gaps.json','observed_phenomena.json','deduplication.json'],
        'verification':['event_validation.json','G/T5/geometry_audit.json','first24h_comparison.json','constraints_comparison.json','search_regression/cases/experiment.json','reopen/instance/experiment.json','lifecycle_index.json','cleanup.json','preservation_check.json','encoding_audit.json']}
    write(out/'artifact_index.json',{k:[dict(path=p,exists=(out/p).is_file()) for p in v] for k,v in index.items()})
    write(out/'artifact_manifest.json',files_manifest(p for p in out.rglob('*') if p.is_file() and p.name!='artifact_manifest.json'))
    return summary['exit_code']


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);args=parser.parse_args()
    if Path(sys.executable).resolve()!=Path(r'D:\conda_envs\fmb\python.exe').resolve():parser.error('fmb interpreter required')
    os.environ['PYTHONDONTWRITEBYTECODE']='1';os.environ['PYTHONIOENCODING']='utf-8'
    cfg=read(ROOT/'configs/round5.json');validate_config(cfg)
    base=ROOT/'work/round5/preflight';inputs=[Path(x['path']) for x in read(base/'preservation_manifest.json')]
    ctx=create_run(args.output,cfg,inputs);ctx['work']=str(ROOT/'work/round5'/ctx['run_id']);Path(ctx['work']).mkdir(parents=True)
    out=Path(ctx['output']);write(out/'run.json',ctx)
    (ROOT/'work/round5/latest_output.txt').write_text(str(out),encoding='utf-8')
    sys.stdout=Tee(sys.stdout,Path(ctx['work'])/'supervisor.stdout.log');sys.stderr=Tee(sys.stderr,Path(ctx['work'])/'supervisor.stderr.log')
    for n in ['baseline.json','history_review.json','unreadable.json','development_notes.json']:
        shutil.copy2(base/n,out/n)
    log=(base/'baseline_tests.log').read_bytes()
    (out/'baseline_tests.log').write_text(log.decode('utf-16' if log.startswith(b'\xff\xfe') else 'utf-8'),encoding='utf-8')
    shutil.copy2(ROOT/'docs/round5_checklist.md',out/'checklist.md')
    write(out/'budget_decision.json',cfg['round5'])
    write(out/'invocation.json',dict(python=sys.executable,argv=sys.argv,workdir=str(ROOT),log_directory=ctx['work']))
    (out/'reproduction.md').write_text('Run with a new empty output directory; no Git mutations.\n\n```powershell\n& "D:\\conda_envs\\fmb\\python.exe" -X utf8 tools/run_round5.py --output "G:\\gpt-file\\Codex\\outputs\\bd-fmb\\round5\\<new_unique_directory>"\n```\n',encoding='utf-8')
    with zipfile.ZipFile(out/'code_snapshot.zip','w',zipfile.ZIP_DEFLATED) as z:
        for f in read(out/'code_manifest.json')['files']:z.write(f['path'],Path(f['path']).relative_to(ROOT))
    started=time.perf_counter();state={}
    try:run(ctx,cfg,state)
    except Exception as exc:
        traceback.print_exc();write(out/'orchestrator_error.json',dict(error=repr(exc),traceback=traceback.format_exc()))
    code=finalize(ctx,cfg,state,started)
    print('RUN_ID',ctx['run_id'],'EXIT',code,'ELAPSED',time.perf_counter()-started,flush=True)
    return code


if __name__=='__main__':raise SystemExit(main())
