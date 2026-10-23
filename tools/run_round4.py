"""Bounded serial round4 execution; each invocation is a fresh evidence run."""
import argparse
import copy
from pathlib import Path
import shutil
import sys
import time
import traceback
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.round2.evidence import ROOT,KEYS,create_run,execute,read,write,result,summarize,validate_stage,artifacts,files_manifest,digest,canonical
from src.round3.lifecycle import supervise,snapshot
from src.round4.frozen import bind
from src.round4.report import KNOWN_SENTENCE,save_report
from tools.run_round3 import Tee

STAGES=['data','environment','generate','tests','D','E','F','lifecycle','search_regression','G','H1','H2','H3','reopen']


def stage(ctx,name,checks,started=None,status=None,detail=None):
    accepted=bool(checks) and all(c['status']=='PASS' for c in checks if c.get('required',True))
    status=status or ('PASS' if accepted else 'FAIL')
    result(ctx,name,status,checks,detail)
    write(Path(ctx['output'])/'commands'/f'{name}.json',dict(**{k:ctx[k] for k in KEYS},stage=name,started=True,
        exit_code=0 if status=='PASS' else 1,timed_out=False,elapsed_s=time.perf_counter()-started if started else 0.,
        argv=[sys.executable,*sys.argv],execution='In-process orchestrator; child timeout/exit/ownership in lifecycle.json, business checks retained'))
    return status


def run(ctx,cfg):
    out=Path(ctx['output']);base=ROOT/'work/round4_prechange'
    for name in ['baseline.json','history_audit.json']:shutil.copy2(base/name,out/name)
    for stage_name in ['data','environment','generate','tests']:
        command=execute(ctx,stage_name,[sys.executable,'-m','src.round2.worker','--output',str(out),'--stage',stage_name],120)
        print('BASE',stage_name,command['exit_code'],round(command['elapsed_s'],2),flush=True)
    tests=validate_stage(ctx,'tests',read(out/'commands/tests.json'))
    tests_ok=tests['status']=='PASS'
    new_checks=[x for x in tests['checks'] if 'test_round4' in x['id']]
    stage(ctx,'D',[dict(id='status_and_h_layer_regressions',status='PASS' if tests_ok and len(new_checks)>=8 else 'FAIL',evidence='tests/result.json',count=len(new_checks))])
    eout=out/'E';eout.mkdir()
    encoding=save_report(eout,dict(core_acceptance='FAIL',exit_code=1),KNOWN_SENTENCE+'\n报告生成成功不能改变业务验收结果。\n')
    stage(ctx,'E',[dict(id='utf8_exact_content_and_subprocess',status='PASS' if tests_ok and encoding['report_generated'] else 'FAIL',evidence='E/encoding_audit.json')])
    frozen,selection=bind(ctx,cfg)
    stage(ctx,'F',[dict(id='unchanged_frozen16_and_original28_selection',status='PASS',point_set_sha256=frozen['point_set_sha256'])],detail=dict(initial_satellites=[x['sat_id'] for x in selection['tiers'][0]['selected']]))
    prereq=all(validate_stage(ctx,s,read(out/'commands'/f'{s}.json'))['status']=='PASS' for s in ['data','environment','generate','tests','D','E','F'])
    prereq &= read(out/'environment/environment.json')['stk']['status']=='PASS'
    if not prereq:return
    start=time.perf_counter();checks=[]
    for name in ['normal','exception','timeout']:
        remaining=cfg['round4']['lifecycle_budget_s']-(time.perf_counter()-start)
        if remaining<30:checks.append(dict(id='lifecycle_budget',status='BLOCKED'));break
        r=supervise(ctx,cfg,name,out/'lifecycle'/name,timeout_s=min(120,remaining))
        checks.append(dict(id=name,status=r['status'],evidence=str(out/'lifecycle'/name/'lifecycle.json')))
        if r['status']!='PASS':break
    if stage(ctx,'lifecycle',checks,start)!='PASS':return
    start=time.perf_counter()
    r=supervise(ctx,cfg,'cases',out/'search_regression/cases',timeout_s=cfg['round4']['regression_budget_s'])
    if stage(ctx,'search_regression',[dict(id='fresh_16_cases_48_searches',status=r['status'],evidence='search_regression/cases/experiment.json')],start)!='PASS':return
    started=time.perf_counter();decisions=[];completed=[];tier_checks=[]
    for tier in selection['tiers']:
        remaining=cfg['round4']['stage_budget_s']-(time.perf_counter()-started)
        estimate=120.
        if completed:
            last=completed[-1];scale=(tier['planes']/last['tier']['planes'])*(tier['duration_s']/last['tier']['duration_s'])
            measured=last['experiment'];overhead=max(0,last['lifecycle']['elapsed_s']-measured['elapsed_s'])
            estimate=1.5*(measured['screening_s']*scale+measured['propagation_setup_s']*tier['planes']/last['tier']['planes']+overhead+60)
        decision=dict(tier_id=tier['id'],remaining_budget_s=remaining,estimated_s=estimate,timeout_s=tier['timeout_s'],
            basis='Prior measured screen scaled by satellites x duration, propagation by satellites, measured lifecycle overhead plus 60s refinement margin; safety factor 1.5' if completed else 'T1 bounded pilot, not a performance promise',
            proceed=estimate<=min(remaining,tier['timeout_s']))
        decisions.append(decision);write(out/'tier_decisions.json',decisions)
        if not decision['proceed']:break
        effective=copy.deepcopy(cfg);effective['duration_s']=tier['duration_s']
        folder=out/'G'/tier['id'];write(out/'tier_configs'/f'{tier["id"]}.json',dict(**{k:ctx[k] for k in KEYS},tier=tier,config=effective,effective_config_sha256=digest(canonical(effective))))
        life=supervise(ctx,effective,'r4_'+tier['id'],folder,dict(tier=tier,frozen=str(out/'grid/frozen.json')),timeout_s=min(tier['timeout_s'],remaining))
        tier_checks.append(dict(id=tier['id'],status=life['status'],evidence=str(folder/'lifecycle.json')))
        if life['status']!='PASS':break
        experiment=read(folder/'experiment.json');completed.append(dict(tier=tier,experiment=experiment,lifecycle=life))
        write(out/'tier_results.json',completed)
        print('TIER',tier['id'],'EVENTS',experiment['event_count'],'H',[(k,experiment['acceptance'][k]['status']) for k in ['H1','H2','H3']],flush=True)
        if all(experiment['acceptance'][k]['status']=='PASS' for k in ['H1','H2','H3']):break
    gstatus=stage(ctx,'G',tier_checks,started,detail=dict(decisions=decisions,finite_search_scope=True))
    if not completed:return
    chosen=completed[-1];tier=chosen['tier'];accept=chosen['experiment']['acceptance']
    # A single final completed tier supplies all three acceptance layers.
    write(out/'selected_evaluation.json',dict(tier_id=tier['id'],selection_rule='Last completed declared tier, no cross-tier event stitching',
        window=chosen['experiment']['window'],acceptance=accept,all_tiers_success=gstatus=='PASS'))
    for key in ['H1','H2','H3']:
        check=accept[key]
        if gstatus!='PASS':check=dict(check,status='BLOCKED',reason_code='G_DEPENDENCY_FAILED',reason='A tier failed; completed-tier evidence retained but current acceptance blocked')
        stage(ctx,key,[check],status=check['status'],detail=dict(tier_id=tier['id'],evidence=f'G/{tier["id"]}/acceptance.json'))
    if gstatus=='PASS':
        effective=copy.deepcopy(cfg);effective['duration_s']=tier['duration_s'];start=time.perf_counter()
        r=supervise(ctx,effective,'r4_reopen',out/'reopen/instance',dict(source=str(out/'G'/tier['id']),tier=tier,frozen=str(out/'grid/frozen.json')),timeout_s=cfg['round4']['reopen_timeout_s'])
        stage(ctx,'reopen',[dict(id='new_instance_parameters_and_recomputed_access',status=r['status'])],start)


def historical_elevation(out,cfg):
    """Explicitly retrospective sampled geometry, not a new STK execution."""
    import csv
    import numpy as np
    from src.round2.geometry import geodetic_to_ecef
    prior=Path(cfg['round4']['source_round3']);p=next((prior/'C/grid2/coarse_trajectory').glob('*.json'));traj=read(p)
    positions=np.array(traj['positions_fixed_km']);times=np.array(traj['times_epsec'])
    groups={'fixed16':read(prior/'grid/frozen.json')['points']}
    with (prior/'grid/full_grid.csv').open(encoding='utf-8',newline='') as f:groups['full3264']=list(csv.DictReader(f))
    data={};a=cfg['earth']['ellipsoid_a_km'];b=cfg['earth']['ellipsoid_b_km']
    for name,points in groups.items():
        maximum=-90.;rows=[]
        for point in points:
            target=geodetic_to_ecef(float(point['latitude_deg']),float(point['longitude_deg']),0,a,b)
            n=np.array([target[0]/a**2,target[1]/a**2,target[2]/b**2]);n/=np.linalg.norm(n)
            delta=positions-target;elev=np.degrees(np.arcsin(np.clip(delta@n/np.linalg.norm(delta,axis=1),-1,1)));index=int(np.argmax(elev))
            rows.append(dict(point_id=point['point_id'],max_elevation_deg=float(elev[index]),time_epsec=float(times[index])))
        data[name]=dict(point_count=len(points),maximum=max(rows,key=lambda x:x['max_elevation_deg']),points=rows)
    write(out/'historical_sampled_elevation.json',dict(kind='RETROSPECTIVE_GEOMETRY_ONLY',source=str(p),sha256=digest(p.read_bytes()),window=traj['window'],step_s=traj['step_s'],groups=data,continuous_absence_proved=False))


def finalize(ctx,cfg,specs):
    out=Path(ctx['output'])
    for name in STAGES:
        if not (out/name/'result.json').exists():
            stage(ctx,name,[dict(id='upstream_not_completed',status='BLOCKED',reason_code='UPSTREAM_NOT_COMPLETED',reason='Dependencies or bounded task did not complete')],status='BLOCKED')
    summary=summarize(ctx,specs)
    life=[read(p) for p in sorted(out.glob('*/**/lifecycle.json'))];write(out/'lifecycle_index.json',life)
    write(out/'final_process_snapshot.json',dict(run_id=ctx['run_id'],instances=snapshot()))
    tiers=read(out/'tier_results.json') if (out/'tier_results.json').exists() else []
    historical_elevation(out,cfg)
    attempts=[]
    for p in out.parent.glob('*/summary.json'):
        if p.parent!=out:
            old=read(p);attempts.append(dict(output=str(p.parent),run_id=old['run_id'],status=old['core_acceptance'],exit_code=old['exit_code'],kind='HISTORICAL_ATTEMPT_NOT_CURRENT_PASS'))
    write(out/'prior_attempts.json',attempts)
    if tiers:
        import os
        os.environ['MPLCONFIGDIR']=str(Path(ctx['work'])/'mpl')
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        fig,axes=plt.subplots(len(tiers),1,figsize=(11,3.2*len(tiers)),squeeze=False)
        for ax,item in zip(axes[:,0],tiers):
            tid=item['tier']['id'];pairs=read(out/'G'/tid/'opportunities.json')
            for sat in item['experiment']['acceptance']['per_satellite_event_counts']:
                rows=[x for x in pairs if x['sat_id']==sat];ax.plot(range(1,17),[x['max_sampled_elevation_deg'] for x in rows],'.-',label=sat)
            ax.axhline(0,color='black',linewidth=.7);ax.set_title(f'{tid}: sampled maximum elevation, window {item["experiment"]["window"]} s');ax.set_ylabel('Elevation (deg)');ax.set_xlabel('Frozen point index');ax.legend(ncol=4);ax.grid(alpha=.2)
        fig.tight_layout();fig.savefig(out/'opportunity_diagnostics.png',dpi=140);plt.close(fig)
    table='\n'.join(f'|{s["stage"]}|{s["status"]}|{s.get("business_status")}|'+ '; '.join(x.get('message','') for x in s.get('reasons',[]))+'|' for s in summary['stages'])
    tiertext=[]
    for item in tiers:
        e=item['experiment'];s=read(out/'G'/item['tier']['id']/'statistics.json');a=e['acceptance']
        tiertext.append(f'- {item["tier"]["id"]}：{e["satellites"]}星、固定16点、窗口{e["window"]}秒。真实事件{e["event_count"]}；逐星贡献{a["per_satellite_event_counts"]}。覆盖{s["covered_points"]}/16，不足两次访问{s["insufficient_access_points"]}/16。内部空窗{s["effective_gap_count"]}个，条件均值{s["conditional_mean_gap_s"]}秒、最大值{s["conditional_max_gap_s"]}秒。H1/H2/H3={a["H1"]["status"]}/{a["H2"]["status"]}/{a["H3"]["status"]}。实例总耗时{item["lifecycle"]["elapsed_s"]:.3f}秒，筛查{e["screening_s"]:.3f}秒，Access及几何{e["access_and_geometry_s"]:.3f}秒，峰值内存{item["lifecycle"]["memory_peak_working_set_bytes"]/1024**2:.1f}MiB。')
    last=tiers[-1] if tiers else None
    next_scale=dict(status='NOT_EXECUTED_RECOMMENDATION',satellites=7,points=32,duration_s=86400,task_budget_s=1200,
        prerequisite='Freeze a shared 32-point subset before access, include islands only under a declared selection rule; separately validate search edge cases and sampling alignment',
        cost_basis=None)
    if last:
        e=last['experiment'];scale=7/e['satellites']*2
        next_scale['estimated_s']=1.5*(e['screening_s']*scale+e['propagation_setup_s']*7/e['satellites']+last['lifecycle']['elapsed_s']-e['elapsed_s']+max(60,e['access_and_geometry_s']*scale))
        next_scale['cost_basis']=dict(tier_id=last['tier']['id'],measured=e,safety_factor=1.5,not_performance_promise=True)
    write(out/'next_scale.json',next_scale)
    report=f'''# 第四轮开发与验证审计

{KNOWN_SENTENCE}

本轮运行 `{ctx['run_id']}`，解释器 `{sys.executable}`。核心验收 **{summary['core_acceptance']}**，退出码 **{summary['exit_code']}**。报告生成成功不改变业务验收。所有档位分别聚合，最终H1/H2/H3只使用最后一个完整档位。失败尝试见 prior_attempts.json，不沿用其成功项。

|阶段|综合状态|业务状态|原因|
|---|---|---|---|
{table}

修改前29项回归实测通过，baseline.json保留命令及日志哈希。本轮回归详见tests/result.json，机制与构造数据不作为真实STK证据。D保留合法业务检查和结构化原因，同时区分超时、无效结果及命令冲突；非零不得PASS。E采用UTF-8源码与原子文件写入，中文不经过终端源代码管道；关键句逐字回读且扫描替换字符/连续问号。第三轮文件未修改，history_audit.json记录损坏位置、原始哈希和状态汇总缺陷；原始AT文字损坏仍是非阻塞数据质量FAIL。

## 冻结来源、模型和机会

16点哈希为 `860d8f4b2b9391adaf68740bf43c500bdcd074f4bd579971d864332a6eb89071`，点序列、经纬度及CSV逐项核对。完整3264点及三多边形组成部分保留；16点全部在主体区域，两个岛屿没有评价点。新包装绑定本轮身份和只读来源哈希，原run_id未篡改。selection.json从原28星/7面/每面4星基线取Sat001、Sat005，再按预声明规则取Sat009、Sat013及Sat017、Sat021、Sat025。轨道高度、倾角、相位、RAAN、历元和相机不为事件调整。

UTCG、地球常数、视场半角、惯性沿迹仍是工程假设。GeoJSON使用经纬度平面直线covers包含边界，区别于STK GreatArc；地面椭球零高、地形关闭并保存读回。不是完整0.1度区域评价。

先对每档全部星点做5秒批量地平线/沿迹筛查，保存最高采样仰角、最近距离、过零括区、精确候选角度与拒绝原因，再调用Sensor Access。交叉迹2倍半角保守门、地平线5度采样裕度保持固定。实际事件由真实Sensor Access局部精化确认。可见不等于入场；采样全负不证明连续无访问。historical_sampled_elevation.json只读复算旧轨迹，明确标注历史采样几何，不能替代本轮STK。

沿迹无变号擦边、一个采样格内多根、仅边缘相交而中心不经过等条件未获全局完备证明。所列内部空窗是本轮搜索观测到的条件统计。search_regression为本轮重跑的16组诊断案例，不进入固定网格分母。独立几何与Access共享底层STK轨迹及坐标变换，独立的是角度边界求根方法，未声称独立物理真值。

## 各档真实结果

{chr(10).join(tiertext) or '没有完整真实网格档位。'}

每档原始逐星区间、查询设置读回、CSV、来源映射、合并区间、统计及手工账本在G/T*/。合并容差为0，重叠和相接合并；未覆盖点、访问不足点均保留16点分母。条件均值按全部有效间隔数汇总，不平均各点均值；没有间隔时均值/最大值为null；未达覆盖阈值为null并附原因；首尾空窗单列。acceptance.json单列同点多星、自然跨星合并和自然重叠/相接是否出现，构造分支不能冒称真实分支。

真实点手工账本用独立端点扫描重新核算全部16点、ID集合、并集和空窗，逐条列出减法。geometry_audit.json复核每个正事件角度边界（2e-4秒）及重新计算的中细收敛（2e-5秒）。未改变既定容差。工程位于每档project/Round4.sc，最后完整档在独立实例重新打开、读回参数并重算全部正事件和固定点短负查询，见reopen/instance。

## 运行可靠性、预算及限制

正常、受控异常、工作进程超时在本轮受控实例实测；超时强制回收明确区别于正常退出。监督器复制并持有确切本轮进程句柄，不把子进程句柄数字当本地句柄，不按名称清理。每次拥有证明、进程前后状态、COM引用计数、退出码、峰值内存在lifecycle_index.json及各实例worker/lifecycle。未知所有权禁止清理。监督器本身被外部强杀未覆盖。

冻结档位预算见checklist.md与config.json；每次扩大前实测外推并记录tier_decisions.json。达到三层验收目标则停止扩大；预算或依赖失败不无限重试。不能直接把毫秒局部窗口成本外推全量。下一档建议及成本公式见next_scale.json：这是未执行估算，需先解决当前未达目标与搜索能力缺口，再按预算决定是否运行。尚不具备宣称正式全区域指标的条件。

## 交付及复现

输入保全 `{summary['source_preserved']}`；运行期间代码未变 `{summary['code_unchanged']}`。代码快照、输入/代码/产物清单、实际argv、完整日志位置 `{ctx['work']}`、退出码和日志哈希均随附。假设版本 `{cfg['assumptions_version']}`。没有优化搜索、全区7天评价或官方四项指标/分数。

复现命令见reproduction.md，新目录隔离且使用fmb解释器。建议手动提交README.md、.gitignore、requirements-smoke.txt、configs、src、tests、tools、docs中的源码与说明，前轮尚未跟踪依赖需一并审阅。data、work、原始资料、交付文件、STK工程和缓存不进入普通提交。代理未执行任何git add、commit、push或PR；手动命令见reproduction.md。
'''
    report_summary=save_report(out,summary,report)
    write(out/'log_manifest.json',dict(run_id=ctx['run_id'],files=files_manifest(list(Path(ctx['work']).rglob('*.log')))))
    write(out/'output_manifest.json',dict(run_id=ctx['run_id'],files=artifacts(out)))
    return report_summary


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);p.add_argument('--config',default=str(ROOT/'configs/round4.json'));args=p.parse_args()
    if Path(sys.executable).resolve()!=Path(r'D:\conda_envs\fmb\python.exe').resolve():p.error('Verified fmb interpreter required')
    import os
    os.environ['PYTHONDONTWRITEBYTECODE']='1';os.environ['PYTHONIOENCODING']='utf-8'
    cfg=read(args.config);inputs=[Path(x['path']) for x in read(ROOT/'work/round4_prechange/preservation_manifest.json')]
    # Completed round4 attempts are also immutable inputs to later reruns.
    parent=Path(args.output).parent
    if parent.exists():
        inputs += [file for directory in parent.iterdir() if directory.is_dir() and (directory/'summary.json').exists()
                   for file in directory.rglob('*') if file.is_file()]
    ctx=create_run(args.output,cfg,inputs);out=Path(ctx['output'])
    (ROOT/'work/round4_latest_output.txt').write_text(str(out),encoding='utf-8')
    sys.stdout=Tee(sys.stdout,Path(ctx['work'])/'supervisor.stdout.log');sys.stderr=Tee(sys.stderr,Path(ctx['work'])/'supervisor.stderr.log')
    specs=[dict(id=x,required=True) for x in STAGES];write(out/'stage_plan.json',dict(run_id=ctx['run_id'],stages=specs))
    write(out/'invocation.json',dict(python=sys.executable,argv=sys.argv,stdout_encoding='utf-8'))
    shutil.copy2(ROOT/'docs/round4_checklist.md',out/'checklist.md');shutil.copy2(ROOT/'docs/round4.md',out/'reproduction.md')
    with zipfile.ZipFile(out/'code_snapshot.zip','w',zipfile.ZIP_DEFLATED) as z:
        for f in read(out/'code_manifest.json')['files']:z.write(f['path'],Path(f['path']).relative_to(ROOT))
    try:run(ctx,cfg)
    except Exception as exc:
        traceback.print_exc();write(out/'orchestrator_error.json',dict(error=repr(exc)))
    summary=finalize(ctx,cfg,specs)
    print('RUN_ID',ctx['run_id'],'ACCEPTANCE',summary['core_acceptance'],'EXIT',summary['exit_code'],flush=True)
    return summary['exit_code']

if __name__=='__main__':raise SystemExit(main())
