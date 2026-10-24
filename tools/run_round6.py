"""Freeze, pass prerequisites, then run exactly one seven-day dataset."""
import argparse
import os
from pathlib import Path
import shutil
import sys
import time
import traceback
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.round2.evidence import ROOT,KEYS,read,write,create_run,code_manifest,files_manifest,digest,canonical,execute,validate_stage,utc
from src.round3.lifecycle import supervise,snapshot
from src.round4.frozen import bind
from src.round4.report import KNOWN_SENTENCE,save_report
from src.round6.audit import validate_config,audit_dataset
from src.round6.faults import historical_empty_query_probe
from src.round6.report import phenomena_text
from tools.run_round3 import Tee

STAGES=['data','environment','generate','tests','frozen','fault_probe','lifecycle','diagnostics','main','reopen','dataset']


def run(ctx,cfg,state):
    out=Path(ctx['output']);r=cfg['round6']
    for name in ['data','environment','generate','tests']:
        cmd=execute(ctx,name,[sys.executable,'-X','utf8','-m','src.round2.worker','--output',str(out),'--stage',name],180)
        state[name]=validate_stage(ctx,name,cmd)
        print('BASE',name,state[name]['status'],cmd['elapsed_s'],flush=True)
    frozen,selection=bind(ctx,cfg);old=Path(r['source_round5'])
    selection['rule']='First satellite of each of seven original 28-satellite Walker planes; 28 identities generated, seven evaluated'
    write(out/'selection.json',selection)
    previous=read(old/'config.json')['config'];historical=read(old/'selection.json')
    fixed=['constellation','earth','epoch_utcg','time_system','reference_frame','propagation_step_s','report_sample_step_s','diagnostic_surface','camera','access_tiers','tolerances']
    physical=all(cfg[k]==previous[k] for k in fixed) and cfg['round3']['search']==previous['round3']['search']
    physical &= cfg['round4']['horizon_guard_deg']==previous['round4']['horizon_guard_deg'] and cfg['round4']['coarse_step_s']==previous['round4']['coarse_step_s']
    state['frozen']=dict(status='PASS' if physical and selection['baseline']==historical['baseline'] and selection['baseline_count']==28 else 'FAIL',
        point_set_sha256=frozen['point_set_sha256'],physical_fields_compared=fixed,window=r['window'])
    state['fault_probe']=historical_empty_query_probe(ctx,cfg,out)
    write(out/'preconditions.json',state)
    if any(state[s]['status']!='PASS' for s in ['data','environment','generate','tests','frozen','fault_probe']):return
    if read(out/'environment/environment.json')['stk']['status']!='PASS':
        state['diagnostics']=dict(status='BLOCKED',reason_code='STK_DEPENDENCY_UNAVAILABLE');return
    start=time.perf_counter();deadline=start+r['total_stk_budget_s']
    def task(name,folder,payload,cap):
        remaining=deadline-time.perf_counter()-r['cleanup_reserve_s']
        if remaining<30:return dict(status='BLOCKED',reason_code='TOTAL_BUDGET_EXHAUSTED')
        if code_manifest()!=read(out/'code_manifest.json')['files']:raise ValueError('Frozen code changed before '+name)
        write(out/'stage_progress.json',state)
        return supervise(ctx,cfg,name,folder,payload,timeout_s=min(cap,remaining))
    try:
        lifes=[];lstart=time.perf_counter()
        for name in ['normal','exception','timeout']:
            remaining=r['lifecycle_budget_s']-(time.perf_counter()-lstart)
            if remaining<30:break
            lifes.append(task(name,out/'lifecycle'/name,None,min(120,remaining)))
        state['lifecycle']=dict(status='PASS' if len(lifes)==3 and all(x['status']=='PASS' for x in lifes) else 'FAIL',tasks=lifes)
        if state['lifecycle']['status']!='PASS':return
        state['diagnostics']=task('r6_diagnostics',out/'diagnostics',None,r['regression_timeout_s'])
        if state['diagnostics']['status']!='PASS':return
        diag=read(out/'diagnostics/experiment.json')
        if diag['status']!='PASS' or (diag['cases'],diag['searches'],diag['history_searches'])!=(16,48,12):
            state['diagnostics']['status']='FAIL';return
        write(out/'main_launch_gate.json',dict(status='PASS',at_utc=utc(),code_sha256=ctx['code_sha256'],
            prerequisites={s:state[s]['status'] for s in STAGES[:8]},diagnostic_finished_utc=utc(),
            diagnostics_elapsed_s=state['diagnostics']['elapsed_s']))
        tier=selection['tiers'][0]
        write(out/'effective_config.json',dict(**{k:ctx[k] for k in KEYS},tier=tier,config=cfg,effective_config_sha256=digest(canonical(cfg))))
        state['main']=task('r6_main',out/'G/T6',dict(tier=tier,frozen=str(out/'grid/frozen.json')),r['main_timeout_s'])
        if state['main']['status']!='PASS':return
        state['reopen']=task('r6_reopen',out/'reopen/instance',dict(source=str(out/'G/T6'),tier=tier,frozen=str(out/'grid/frozen.json')),r['reopen_timeout_s'])
        state['dataset']=audit_dataset(ctx,cfg)
    finally:
        state['real_stk_elapsed_s']=time.perf_counter()-start;write(out/'stage_progress.json',state)


def finalize(ctx,cfg,state,started):
    out=Path(ctx['output']);preservation=[]
    for f in read(out/'input_manifest.json')['files']:
        try:ok=digest(Path(f['path']).read_bytes())==f['sha256'];error=None
        except OSError as exc:ok=False;error=repr(exc)
        preservation.append(dict(path=f['path'],unchanged=ok,error=error))
    inputs_ok=all(x['unchanged'] for x in preservation);code_ok=code_manifest()==read(out/'code_manifest.json')['files']
    write(out/'preservation_check.json',dict(status='PASS' if inputs_ok and code_ok else 'FAIL',inputs=preservation,code_unchanged=code_ok))
    life=[dict(path=str(p),**read(p)) for p in out.rglob('lifecycle.json')]
    write(out/'lifecycle_index.json',life);owned={x['ownership']['pid'] for x in life if x.get('ownership')};after=snapshot()
    residual=[p for p in after if p['pid'] in owned];cleanup=bool(life) and all(x.get('residual') is False for x in life) and not residual
    write(out/'cleanup.json',dict(status='PASS' if cleanup else 'FAIL',after=after,owned_residual=residual,unrelated_instances_untouched=True))
    for s in STAGES:state.setdefault(s,dict(status='BLOCKED',reason_code='UPSTREAM_NOT_COMPLETED'))
    common=all(state[s]['status']=='PASS' for s in STAGES) and inputs_ok and code_ok and cleanup
    acceptance=state['dataset'].get('acceptance',{})
    for k in ['H1','H2','H3']:
        goal=acceptance.get(k,dict(id=k,status='BLOCKED',reason_code='NO_COMPLETE_CURRENT_DATASET'))
        if not common:goal=dict(id=k,status='FAIL' if state['dataset']['status']=='FAIL' else 'BLOCKED',reason_code='COMMON_EVIDENCE_PREREQUISITE_FAILED',dataset_result=goal)
        acceptance[k]=goal
    overall='PASS' if common and all(acceptance[k]['status']=='PASS' for k in ['H1','H2','H3']) else 'FAIL'
    stats=read(out/'G/T6/statistics.json') if (out/'G/T6/statistics.json').exists() else None
    counts={s:None for s in cfg['round6']['satellites']}
    if (out/'satellite_contributions.json').exists():counts={x['sat_id']:x['event_count'] for x in read(out/'satellite_contributions.json')}
    summary=dict(**{k:ctx[k] for k in KEYS},core_acceptance=overall,exit_code=0 if overall=='PASS' else 1,
        acceptance=acceptance,stages=state,statistics=stats,per_satellite_event_counts=counts,evidence_valid=common,
        formal_experiment_started=state['main'].get('started_utc') is not None,formal_seven_days_completed=state['main']['status']=='PASS',
        inputs_preserved=inputs_ok,code_unchanged=code_ok,cleanup_passed=cleanup,elapsed_s=time.perf_counter()-started,
        real_stk_elapsed_s=state.get('real_stk_elapsed_s'),data_quality=dict(original_AT_description='FAIL',required=False,source_unchanged=inputs_ok))
    write(out/'summary.json',summary)
    text='# 第六轮七星、原16点、完整7天审计\n\n'+KNOWN_SENTENCE+'\n\n'
    text+=f"运行 `{ctx['run_id']}`，整体 **{overall}**，业务退出码 **{summary['exit_code']}**。正式实验已启动：{summary['formal_experiment_started']}；完整7天完成：{summary['formal_seven_days_completed']}。\n\n"
    text+='|检查|状态|\n|---|---|\n'+''.join(f"|{s}|{state[s]['status']}|\n" for s in STAGES)+'\n'
    text+='\n'.join(f"- {k}: {acceptance[k]['status']} / {acceptance[k]['reason_code']}" for k in ['H1','H2','H3'])+'\n\n'
    text+='唯一生产入口为 `src.round6.production.search_batch`：正式任务与16类48次诊断、四个历史候选12次复核使用同一筛查→候选→Sensor Access→事件链。原0.1秒padding、5度地平线保护、2倍交叉角候选门和0.05秒候选半窗冻结。局部原始Access与评价窗裁剪结果均保存，裁剪边界不解释为物理端点。有限诊断不证明全局无漏检。\n\n'
    text+='候选账本独立对照全部机会来源；空查询与正查询同等必需。historical_empty_query_fault_probe.json先验证完整适配账本PASS，再逐一删除第五轮T5_Q0000/T5_Q0001，必须因MISSING_CANDIDATE_QUERY失败。这是受控内存适配，不是本轮真实科学数据。轨迹审计独立生成预期格点，按唯一文件核查实际数组和112组引用；正式7天每星120961个时间点。\n\n'
    text+='|卫星|真实事件数|\n|---|---:|\n'+''.join(f'|{s}|{n}|\n' for s,n in counts.items())+'\n'
    if stats:
        text+=f"覆盖 {stats['covered_points']}/16；未覆盖 {stats['uncovered_points']}/16；少于两次合并访问 {stats['insufficient_access_points']}/16。有效内部空窗 {stats['effective_gap_count']} 个；条件均值 {stats['conditional_mean_gap_s']} 秒；条件最大值 {stats['conditional_max_gap_s']} 秒。首尾删失空窗单列，未达覆盖阈值为null。\n\n"
    text+='独立16点端点扫描见independent_ledger.json；逐条空窗减法及决定端点的事件、卫星、几何/重开路径见internal_gaps.json。首48小时在完整主搜索后按112组对照第五轮，见first48h_comparison.json。若主任务未启动或未完成，上述主数据不宣称存在或有效。\n\n'
    text+=phenomena_text(read(out/'observed_phenomena.json') if (out/'observed_phenomena.json').exists() else {})
    text+='场景为暂定UTCG 2027-01-01至2027-01-08，EpSec [0,604800]。原28星Walker身份生成并核对，评估七面首星。原16点顺序/坐标/哈希不变，均在主体区域，两岛无子集点。J2、600 km、30秒传播步长、相机、姿态和约束保持原口径。正式粗筛5秒零相位，未扩点扩星、未滚动换窗、未拼接历史、未提前停止。\n\n'
    text+='重开在另一新建实例中读回七星根数、姿态、相机、约束、16点和7天传播期，复算全部正事件；短负例仅各星首点[0,0.1]秒。生命周期正常/异常/预设超时与主实验超时分开；仅通过监督器复制并持有的确切句柄清理本轮实例。顶层监督器被外部强杀的回收能力未验证。\n\n'
    text+=f"输入保全：{inputs_ok}；运行代码冻结：{code_ok}；实例清理：{cleanup}。实际命令和外层耗时/退出码见command_result.json，日志位于 `{ctx['work']}`。历史不可读范围重新生成于unreadable.json；哈希匹配、缺失、不一致及不可读逐项见history_manifest_audit.json。原AT中文损坏仍为非阻塞数据质量FAIL，原件未修改。\n\n"
    text+='这是七星、原16个固定调试点、7天内搜索观测到的工程条件统计，不代表全区、官方指标或评分。UTCG、地球常数、视场半角待主办方确认；角度核验共享STK轨迹及坐标变换，不是独立物理真值或纳秒物理精度。无变号擦边、单采样格多根、仅边缘相交等全局搜索完整性及长期动力学校准未被本轮证明。\n'
    report=save_report(out,summary,text)
    final=dict(command=dict(started=True,completed=True,exit_code=summary['exit_code'],elapsed_s=time.perf_counter()-started,
        timed_out=any(x.get('timed_out') for x in life if x.get('task')!='timeout')),
        evidence=dict(status='PASS' if common else 'FAIL',inputs_preserved=inputs_ok,code_unchanged=code_ok),
        business=dict(core_acceptance=overall,**{k:acceptance[k] for k in ['H1','H2','H3']}),
        reporting=dict(generated=report['report_generated'],encoding=report['report_encoding']['status']),
        cleanup=dict(status='PASS' if cleanup else 'FAIL',owned_residual=residual))
    write(out/'final_status.json',final)
    write(out/'checklist_results.json',{s:state[s]['status'] for s in STAGES})
    failures=[dict(stage=s,status=state[s]['status'],evidence=str(out/'stage_progress.json')) for s in STAGES if state[s]['status']!='PASS']
    if (out/'baseline/retry_decision.json').exists():failures.insert(0,dict(kind='PRESERVED_PRIOR_ATTEMPT',**read(out/'baseline/retry_decision.json')))
    write(out/'failure_attempts.json',failures)
    write(out/'log_manifest.json',files_manifest(p for p in Path(ctx['work']).rglob('*') if p.is_file()))
    return summary['exit_code']


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);p.add_argument('--baseline',required=True);args=p.parse_args()
    if Path(sys.executable).resolve()!=Path(r'D:\conda_envs\fmb\python.exe').resolve():p.error('fmb interpreter required')
    os.environ['PYTHONDONTWRITEBYTECODE']='1';os.environ['PYTHONIOENCODING']='utf-8'
    cfg=read(ROOT/'configs/round6.json');validate_config(cfg);base=Path(args.baseline)
    ctx=create_run(args.output,cfg,[x['path'] for x in read(base/'input_manifest.json')]);ctx['work']=str(ROOT/'work/round6'/ctx['run_id']);Path(ctx['work']).mkdir()
    out=Path(ctx['output']);write(out/'run.json',ctx);(ROOT/'work/round6/latest_output.txt').write_text(str(out),encoding='utf-8')
    sys.stdout=Tee(sys.stdout,Path(ctx['work'])/'supervisor.stdout.log');sys.stderr=Tee(sys.stderr,Path(ctx['work'])/'supervisor.stderr.log')
    shutil.copytree(base,out/'baseline');shutil.copy2(base/'unreadable.json',out/'unreadable.json')
    shutil.copy2(base/'history_manifest_audit.json',out/'history_manifest_audit.json')
    shutil.copy2(ROOT/'docs/round6_checklist.md',out/'checklist.md')
    write(out/'budget_decision.json',cfg['round6']);write(out/'invocation.json',dict(argv=sys.argv,python=sys.executable,cwd=str(ROOT),started_utc=utc()))
    (out/'reproduction.md').write_text('Use a new output directory and the preserved baseline.\n\n```powershell\n& "D:\\conda_envs\\fmb\\python.exe" -X utf8 tools/run_round6.py --baseline "'+str(base)+'" --output "G:\\gpt-file\\Codex\\outputs\\bd-fmb\\round6\\<new_unique_directory>"\n```\n',encoding='utf-8')
    with zipfile.ZipFile(out/'code_snapshot.zip','w',zipfile.ZIP_DEFLATED) as z:
        for f in read(out/'code_manifest.json')['files']:z.write(f['path'],Path(f['path']).relative_to(ROOT))
    started=time.perf_counter();state={}
    try:run(ctx,cfg,state)
    except Exception as exc:
        traceback.print_exc();write(out/'orchestrator_error.json',dict(error=repr(exc),traceback=traceback.format_exc()))
    code=finalize(ctx,cfg,state,started)
    print('RUN_ID',ctx['run_id'],'EXIT',code,'ELAPSED',time.perf_counter()-started,flush=True);return code


if __name__=='__main__':raise SystemExit(main())
