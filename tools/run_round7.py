"""Freeze -> reliability/performance gates -> one full28/16/7d dataset."""
import argparse,datetime,json,os,shutil,sys,time,traceback,zipfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.round2.evidence import ROOT,KEYS,read,write,create_run,code_manifest,files_manifest,digest,canonical,execute,validate_stage,utc
from src.round3.lifecycle import supervise,snapshot
from src.round4.report import KNOWN_SENTENCE,save_report
from src.round6.faults import historical_empty_query_probe
from src.round6.report import phenomena_text
from src.round7.audit import validate_config,bind,audit_dataset,CAPS
from src.round7.contract import prepare
from src.round7.mechanisms import declarations,run_mechanisms
from src.round7.replay import replay
from src.round7.performance import pair_check,forecast
from tools.run_round3 import Tee

STAGES=['contract','data','environment','generate','tests','frozen','fault_probe','mechanisms','offline_replay','lifecycle','diagnostics','known_extra','cold_old','cold_new','cold_pair','B1','B2','forecast','main_gate','main','reopen','dataset']
BUDGET=ROOT/'work/round7/cumulative_stk_budget.json'


def budget():
    return read(BUDGET) if BUDGET.exists() else dict(caps_s=CAPS,entries=[])


def run(ctx,cfg,state,base):
    out=Path(ctx['output']);state['contract']=prepare(ctx,cfg,base)
    shutil.copy2(ROOT/'docs/round7_checklist.md',out/'checklist.md')
    write(out/'reliability_declaration.json',dict(analytic=declarations(),real_diagnostics='Existing16x3 and historical4x3, steps5/2/1 phases0/.371/.619; independent full-local-window fine native Access',
        known12='Each historical event defines fixture center only, evaluation center±30s; compare independent whole-window native Access and angular geometry',
        extra=[dict(seed=900.337,cross=.99999,along=.8,window_half_s=30,expected_events=1),dict(seed=2600.419,cross=-.99999,along=-.8,window_half_s=30,expected_events=1)],
        independent_angle_tolerance_s=.0002,native_convergence_tolerance_s=.00002,main_seeds_prohibited=True))
    for name in ['data','environment','generate','tests']:
        cmd=execute(ctx,name,[sys.executable,'-X','utf8','-m','src.round2.worker','--output',str(out),'--stage',name],240)
        state[name]=validate_stage(ctx,name,cmd);print('BASE',name,state[name]['status'],cmd['elapsed_s'],flush=True)
    frozen,selection=bind(ctx,cfg);state['frozen']=dict(status='PASS',point_sha256=frozen['point_set_sha256'],satellites=len(selection['tiers'][0]['selected']))
    state['fault_probe']=historical_empty_query_probe(ctx,cfg,out)
    state['mechanisms']=run_mechanisms(ctx,cfg,out/'reliability/analytic')
    if any(state[s]['status']!='PASS' for s in STAGES[:8]):return
    state['offline_replay']=replay(cfg,out/'reliability/offline')
    oldroot=Path(ctx['work'])/'isolated_round6_snapshot';oldroot.mkdir()
    with zipfile.ZipFile(Path(cfg['round7']['source_round6'])/'code_snapshot.zip') as z:
        for name in z.namelist():
            if not (oldroot/name).resolve().is_relative_to(oldroot.resolve()):raise ValueError('Unsafe snapshot member')
        z.extractall(oldroot)
    write(out/'old_snapshot_reference.json',dict(source=str(Path(cfg['round7']['source_round6'])/'code_snapshot.zip'),sha256=digest((Path(cfg['round7']['source_round6'])/'code_snapshot.zip').read_bytes()),isolated_path=str(oldroot)))
    ledger=budget()
    if ledger['caps_s']!=CAPS:raise ValueError('Cumulative budget cap changed')
    def task(label,category,taskname,folder,payload=None):
        nonlocal ledger
        ledger=budget();used=sum(x['elapsed_s'] for x in ledger['entries']);cat=sum(x['elapsed_s'] for x in ledger['entries'] if x['category']==category)
        reserve=CAPS['cleanup_reserve']+(CAPS['reopen'] if category not in ['reopen','lifecycle'] else 0)
        available=min(CAPS[category]-cat,CAPS['cumulative']-used-reserve)
        if available<60:return dict(status='BLOCKED',reason_code='CUMULATIVE_STAGE_OR_TOTAL_BUDGET',available_s=available)
        if code_manifest()!=read(out/'code_manifest.json')['files']:raise ValueError('Frozen code changed before '+label)
        write(out/'stage_progress.json',state)
        # Leave room inside the stage cap for the exact-handle finally cleanup.
        life=supervise(ctx,cfg,taskname,folder,payload,timeout_s=available-45)
        entry=dict(run_id=ctx['run_id'],stage=label,category=category,elapsed_s=life['elapsed_s'],status=life['status'],started_utc=life['started_utc'],evidence=str(Path(folder)/'lifecycle.json'))
        ledger['entries'].append(entry);write(BUDGET,ledger);write(out/'cumulative_stk_budget.json',ledger)
        return life
    lifes=[]
    for name in ['normal','exception','timeout']:
        life=task(name,'lifecycle',name,out/'lifecycle'/name);lifes.append(life)
        if life['status']!='PASS':break
    state['lifecycle']=dict(status='PASS' if len(lifes)==3 and all(x['status']=='PASS' for x in lifes) else 'FAIL',tasks=lifes)
    if state['lifecycle']['status']!='PASS':return
    for label,taskname,folder,payload in [('diagnostics','r7_diagnostics',out/'diagnostics',None),('known_extra','r7_known_extra',out/'reliability/known_extra',None),
        ('cold_old','r7_dataset_cold_old',out/'benchmarks/cold_old',dict(stage='cold_old',frozen=str(out/'grid/frozen.json'),old_root=str(oldroot))),
        ('cold_new','r7_dataset_cold_new',out/'benchmarks/cold_new',dict(stage='cold_new',frozen=str(out/'grid/frozen.json')))]:
        state[label]=task(label,'reliability_and_cold',taskname,folder,payload)
        if state[label].get('residual') not in [False,None]:return
    if state['cold_old']['status']==state['cold_new']['status']=='PASS':state['cold_pair']=pair_check(out,cfg)
    else:state['cold_pair']=dict(status='BLOCKED',reason_code='COLD_PAIR_INCOMPLETE')
    for name in ['B1','B2']:
        state[name]=task(name,name,'r7_dataset_'+name,out/'benchmarks'/name,dict(stage=name,frozen=str(out/'grid/frozen.json')))
        if state[name].get('residual') not in [False,None]:return
    used=sum(x['elapsed_s'] for x in budget()['entries'])
    if state['B2']['status']=='PASS':state['forecast']=forecast(out,cfg,CAPS['cumulative']-used)
    else:state['forecast']=dict(status='BLOCKED',reason_code='B2_INCOMPLETE')
    needed=STAGES[:18];good=all(state.get(s,{}).get('status')=='PASS' for s in needed)
    gate=dict(status='PASS' if good else 'BLOCKED',at_utc=utc(),**{k:ctx[k] for k in KEYS},code_frozen=code_manifest()==read(out/'code_manifest.json')['files'],
        prerequisites={s:dict(status=state.get(s,{}).get('status','BLOCKED'),evidence=str(out/'stage_progress.json')) for s in needed},
        main_forecast=str(out/'performance_forecast.json'),cumulative_used_s=used,hard_caps=CAPS)
    if not gate['code_frozen']:gate['status']='BLOCKED'
    state['main_gate']=gate;write(out/'stage_progress.json',state);write(out/'main_launch_gate.json',gate)
    if gate['status']!='PASS':return
    state['main']=task('main','main','r7_dataset_T7',out/'G/T7',dict(stage='T7',frozen=str(out/'grid/frozen.json')))
    if state['main']['status']!='PASS':return
    state['reopen']=task('reopen','reopen','r7_reopen',out/'reopen/instance',dict(source=str(out/'G/T7'),tier=selection['tiers'][0],frozen=str(out/'grid/frozen.json')))
    state['dataset']=audit_dataset(ctx,cfg)


def finalize(ctx,cfg,state,started):
    out=Path(ctx['output']);preservation=[]
    for f in read(out/'input_manifest.json')['files']:
        try:b=Path(f['path']).read_bytes();ok=digest(b)==f['sha256'];error=None
        except OSError as e:ok=False;error=repr(e)
        preservation.append(dict(path=f['path'],unchanged=ok,error=error))
    source_ok=all(x['unchanged'] for x in preservation);code_ok=code_manifest()==read(out/'code_manifest.json')['files']
    write(out/'preservation_check.json',dict(status='PASS' if source_ok and code_ok else 'FAIL',inputs=preservation,code_unchanged=code_ok))
    lifes=[dict(path=str(p),**read(p)) for p in out.rglob('lifecycle.json')];write(out/'lifecycle_index.json',lifes)
    after=snapshot();owned={(x['ownership']['pid'],x['ownership']['creation_time']) for x in lifes if x.get('ownership')}
    residual=[x for x in after if (x['pid'],x['creation_time']) in owned];cleanup=all(x.get('residual') is False for x in lifes) and not residual
    write(out/'cleanup.json',dict(status='PASS' if cleanup else 'FAIL',after=after,owned_residual=residual,unrelated_instances_untouched=True,activated_instances=len(lifes)))
    for s in STAGES:state.setdefault(s,dict(status='BLOCKED',reason_code='UPSTREAM_NOT_COMPLETED'))
    if not (out/'main_launch_gate.json').exists():write(out/'main_launch_gate.json',dict(status='BLOCKED',at_utc=utc(),**{k:ctx[k] for k in KEYS},prerequisites={s:state[s]['status'] for s in STAGES[:18]}))
    ledger=budget();used=sum(x['elapsed_s'] for x in ledger['entries']);bycat={c:sum(x['elapsed_s'] for x in ledger['entries'] if x['category']==c) for c in CAPS if c not in ['cleanup_reserve','cumulative']}
    budget_ok=used<=CAPS['cumulative'] and all(v<=CAPS[c] for c,v in bycat.items());write(out/'cumulative_stk_budget.json',dict(ledger,used_s=used,by_category_s=bycat,status='PASS' if budget_ok else 'FAIL'))
    common=all(state[s]['status']=='PASS' for s in STAGES) and source_ok and code_ok and cleanup and budget_ok
    acceptance=state['dataset'].get('acceptance',{})
    for k in ['H1','H2','H3']:
        item=acceptance.get(k,dict(status='BLOCKED',reason_code='NO_COMPLETE_CURRENT28_DATASET'))
        if not common:item=dict(status='BLOCKED',reason_code='COMMON_EVIDENCE_FAILED',dataset_result=item)
        acceptance[k]=item
    overall='PASS' if common and all(acceptance[k]['status']=='PASS' for k in ['H1','H2','H3']) else 'FAIL'
    stats=read(out/'G/T7/statistics.json') if (out/'G/T7/statistics.json').exists() else None
    summary=dict(**{k:ctx[k] for k in KEYS},core_acceptance=overall,exit_code=0 if overall=='PASS' else 1,acceptance=acceptance,stages=state,statistics=stats,
        formal_experiment_started=state['main'].get('started_utc') is not None,formal_seven_days_completed=state['main']['status']=='PASS',
        official_readiness='UNRESOLVED',official_metrics_applicable=False,official_score=None,inputs_preserved=source_ok,code_unchanged=code_ok,cleanup_passed=cleanup,
        current_attempt_stk_s=sum(x['elapsed_s'] for x in ledger['entries'] if x['run_id']==ctx['run_id']),cumulative_real_stk_s=used,elapsed_s=time.perf_counter()-started,
        data_quality=dict(original_AT_description='FAIL',required=False,source_unchanged=source_ok))
    write(out/'summary.json',summary);write(out/'stage_progress.json',state)
    text='# 第七轮原28星、原16点、完整7天工程基线\n\n'+KNOWN_SENTENCE+'\n\n'
    text+=f"运行 `{ctx['run_id']}`；工程整体 **{overall}**；退出码 **{summary['exit_code']}**。主实验已启动：{summary['formal_experiment_started']}；完整7天完成：{summary['formal_seven_days_completed']}。官方口径就绪仍为UNRESOLVED，不输出官方全区指标或评分。\n\n"
    text+='|阶段|状态|\n|---|---|\n'+''.join(f'|{s}|{state[s]["status"]}|\n' for s in STAGES)+'\n'
    text+='\n'.join(f'- {k}：{acceptance[k]["status"]}' for k in ['H1','H2','H3'])+'\n\n'
    text+=f"当前回归实际检查 {len(state['tests'].get('checks',[]))} 项。解析/构造案例、离线回放和真实诊断分别见reliability/analytic、reliability/offline、diagnostics；原12事件与两个额外近角点对照见reliability/known_extra。\n\n"
    if (out/'baseline/retry_decision.json').exists():
        prior=read(out/'baseline/retry_decision.json')
        text+=f"保留首次失败尝试 `{prior['source_run_id']}`，目录 `{prior['source_attempt']}`，退出 {prior['previous_exit']}，真实STK耗时 {prior['prior_real_stk_s']:.6f} 秒。首次因把空场景固定内存也按时长乘7而未通过内存门，正式主任务未启动；更正固定/增长成本分解后，本轮全部前置重新执行，未提高硬上限、未重置累计预算。详见baseline/retry_decision.json。\n\n"
    if not summary['formal_experiment_started']:
        text+='正式28星7天未运行。具体未通过关口：'+', '.join(s+'='+state[s]['status'] for s in STAGES[:19] if state[s]['status']!='PASS')+'。见main_launch_gate.json、stage_progress.json及对应worker日志。\n\n'
    if stats:
        text+=f"真实事件 {sum(p['raw_interval_count'] for p in stats['points'])} 条；覆盖 {stats['covered_points']}/16；未覆盖 {stats['uncovered_points']}/16；少于两次合并访问 {stats['insufficient_access_points']}/16；内部空窗 {stats['effective_gap_count']} 个；条件均值 {stats['conditional_mean_gap_s']} s，最大 {stats['conditional_max_gap_s']} s。\n\n"
        if (out/'satellite_contributions.json').exists():
            text+='|卫星|真实事件数|\n|---|---:|\n'+''.join(f"|{r['sat_id']}|{r['event_count']}|\n" for r in read(out/'satellite_contributions.json'))+'\n'
        if (out/'same_run_seven_statistics.json').exists():
            seven=read(out/'same_run_seven_statistics.json');hist=read(out/'historical_seven_comparison.json')
            text+=f"同轮原七星子集：{sum(p['raw_interval_count'] for p in seven['points'])}事件，{seven['covered_points']}/16覆盖，{seven['effective_gap_count']}内部gap，条件均值{seven['conditional_mean_gap_s']}s，最大{seven['conditional_max_gap_s']}s。历史12事件对照 {hist['status']}；搜索修复新增事件 {len(hist['added_search_repair_events'])} 条。与全部28星的逐点并集及gap变化见same_run_expansion.json。\n\n"
    if (out/'observed_phenomena.json').exists():text+=phenomena_text(read(out/'observed_phenomena.json'))
    text+='第六轮仅在baseline中提供纠正文案副本和源哈希，原报告、12事件、3空窗及原代码快照未改。评价合同逐项引用DOCX正文及表格，未知时间系统、全/半角和边界处理保持工程假设；16点为主体16/两岛0/0，不能代表3264点全区。\n\n'
    text+='搜索使用同一src.round7.production.search_batch。解析机制、5秒轨迹离线回放、真实STK短窗证据分开；三次候选包络的0.05km余量是有限验证下的工程假设，不是任意J2子格运动误差的数学上界。毫秒事件来自原生Access；未证明7天全局无漏检。重复查询窗口仅按重叠/相接并集，近邻不同事件不删。\n\n'
    text+='旧12事件回归与同轮7/28扩星比较分列。覆盖并集包含关系是单调检查；gap数量、合并段数和条件均值/最大值没有强制单调要求。角度方法共享STK轨迹，不称独立物理真值。所有正事件重开与各星首点[0,0.1]秒短负查询独立列出；短负查询只支持其有限时间范围。\n\n'
    text+='|预算类别|实测累计秒|硬上限秒|\n|---|---:|---:|\n'+''.join(f'|{k}|{v:.6f}|{CAPS[k]}|\n' for k,v in bycat.items())+f'|全部真实STK|{used:.6f}|18000|\n\n'
    text+='冷缓存配对见cold_pair_comparison.json；B1/B2均BENCHMARK_ONLY，分项外推见performance_forecast.json，未拼入主结果。provider请求次数与请求状态数、Python/STK峰值内存和写盘成本分别记录。离线速度不代表STK端到端速度，单次配对不作统计显著提速结论。\n\n'
    if (out/'cold_pair_comparison.json').exists():
        pair=read(out/'cold_pair_comparison.json')
        text+=f"真实冷缓存配对 {pair['status']}：旧/新监督器墙钟 {pair['old']['supervisor_s']:.6f}/{pair['new']['supervisor_s']:.6f}s；筛查 {pair['old']['experiment']['screening_s']:.6f}/{pair['new']['experiment']['screening_s']:.6f}s。\n\n"
    if (out/'performance_forecast.json').exists():
        pred=read(out/'performance_forecast.json');text+=f"B2分项外推主任务 {pred['main_forecast_s']:.6f}s，1.5倍后 {pred['guarded_main_s']:.6f}s，与10800s比较；资源门 {pred['status']}。内存固定/增长分解及128MiB不确定量见memory_components；没有把主任务预测替代实际完成证据。\n\n"
    text+='普通受限历史清单核对及不可读路径见baseline/round6_manifest_ordinary_scope.json；当前授权读取另列，不能继承旧轮“全量匹配”。所有失败/重试累计预算见cumulative_stk_budget.json。STK仅新建独立实例，由监督器持有确切句柄回收。未接管用户实例，未执行Git写操作。\n'
    save_report(out,summary,text)
    final=dict(command=dict(exit_code=summary['exit_code'],timed_out=any(x.get('timed_out') for x in lifes if x['task']!='timeout')),
        evidence=dict(status='PASS' if common else 'FAIL',inputs_preserved=source_ok,code_unchanged=code_ok),business=dict(core_acceptance=overall,**{k:acceptance[k] for k in ['H1','H2','H3']}),
        official_readiness='UNRESOLVED',cleanup=dict(status='PASS' if cleanup else 'FAIL',owned_residual=residual))
    write(out/'final_status.json',final);write(out/'checklist_results.json',{s:state[s]['status'] for s in STAGES})
    failures=[dict(stage=s,status=state[s]['status'],evidence=str(out/'stage_progress.json')) for s in STAGES if state[s]['status']!='PASS']
    if (out/'baseline/retry_decision.json').exists():failures.insert(0,dict(kind='PRESERVED_PRIOR_ATTEMPT',**read(out/'baseline/retry_decision.json')))
    write(out/'failure_attempts.json',failures)
    write(out/'static_runtime_review.json',dict(static_status='PASS',runtime_status='PASS' if common else 'FAIL',code_sha256=ctx['code_sha256'],
        reviewed=['No user instance attachment or PID-name termination; existing exact-handle ownership retained',
            'Round5/6 contracts stay explicit; round7 independently enforces all28/16/604800',
            'Empty queries, failed queries and returned trajectory grids remain fail-closed',
            'Candidate interval unions retain all sources and all distinct native events; no0.01s root deletion',
            'Bounded explicit stage/cumulative budgets include retries; benchmark data never enters main',
            'Frozen physical parameters, endpoint tolerances and source scientific data preserved'],
        limitations=['Finite candidate tests do not prove global search completeness','Official evaluation readiness remains unresolved'],regression_checks=len(state['tests'].get('checks',[]))))
    return summary['exit_code']


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',required=True);ap.add_argument('--baseline',required=True);args=ap.parse_args()
    if Path(sys.executable).resolve()!=Path(r'D:\conda_envs\fmb\python.exe').resolve():raise ValueError('fmb interpreter required')
    cfg=read(ROOT/'configs/round7.json');validate_config(cfg);base=Path(args.baseline)
    refs=[x['path'] for x in read(base/'round6_manifest_ordinary_scope.json')['files'] if x['status']=='MATCH']+[x['path'] for x in read(base/'key_sources.json')]
    refs += [str(p) for p in (ROOT/'data').rglob('*') if p.is_file()]
    refs += [str(p) for p in (Path(cfg['round4']['source_round3'])/'grid').iterdir() if p.is_file()]
    ctx=create_run(args.output,cfg,refs);ctx['work']=str(ROOT/'work/round7'/ctx['run_id']);Path(ctx['work']).mkdir();out=Path(ctx['output']);write(out/'run.json',ctx)
    (ROOT/'work/round7/latest_output.txt').write_text(str(out),encoding='utf-8')
    sys.stdout=Tee(sys.stdout,Path(ctx['work'])/'supervisor.stdout.log');sys.stderr=Tee(sys.stderr,Path(ctx['work'])/'supervisor.stderr.log')
    write(out/'invocation.json',dict(argv=sys.argv,python=sys.executable,cwd=str(ROOT),started_utc=utc()))
    with zipfile.ZipFile(out/'code_snapshot.zip','w',zipfile.ZIP_DEFLATED) as z:
        for f in read(out/'code_manifest.json')['files']:z.write(f['path'],Path(f['path']).relative_to(ROOT))
    (out/'reproduction.md').write_text('Use a NEW unique directory. Preserve cumulative_stk_budget.json across retries; do not reset consumed budgets.\n\n'
        +f'Interpreter: {sys.executable}\n\nCommand: python -X utf8 tools/run_round7.py --baseline "{base}" --output "<new unique round7 directory>"\n\n'
        +'Required local paths and hashes: input_manifest.json; original28 baseline/frozen16, prior scientific files, officialDOCX, and local STK11.6 installation. No history data used as main seeds.\n',encoding='utf-8')
    state={};started=time.perf_counter()
    try:run(ctx,cfg,state,base)
    except Exception as exc:
        traceback.print_exc();write(out/'orchestrator_error.json',dict(error=repr(exc),traceback=traceback.format_exc()))
    rc=finalize(ctx,cfg,state,started);print('RUN_ID',ctx['run_id'],'EXIT',rc,'ELAPSED',time.perf_counter()-started,flush=True);return rc


if __name__=='__main__':raise SystemExit(main())
