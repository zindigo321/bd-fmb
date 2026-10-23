"""Verify a completed run and generate a self-contained audit without changing stages."""
import argparse
from pathlib import Path
import shutil
import sys
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.round2.evidence import ROOT,read,write,load_run,summarize,artifacts,digest,files_manifest


def finalize(output):
    out=Path(output);ctx,cfg=load_run(out)
    summary=summarize(ctx,read(out/'stage_plan.json')['stages'])
    rows={s['stage']:s for s in summary['stages']}
    life=[read(p) for p in sorted(out.glob('*/**/lifecycle.json'))]
    write(out/'lifecycle_index.json',life)
    attempts=[]
    for p in sorted((ROOT/'work').glob('round3_lifecycle_dev*/run.json')):
        run=read(p);sp=p.parent/'summary.json'
        attempts.append(dict(run_id=run['run_id'],output=str(p.parent),work=run['work'],
            summary=read(sp) if sp.exists() else None,
            files=files_manifest(list(p.parent.rglob('*')))))
    write(out/'development_attempts.json',attempts)
    formal=[]
    for p in sorted(out.parent.glob('round3_*/summary.json')):
        if p.parent==out:continue
        old=read(p)
        formal.append(dict(output=str(p.parent),run_id=old.get('run_id'),core_acceptance=old.get('core_acceptance'),
            exit_code=old.get('exit_code'),stages={s['stage']:s['status'] for s in old.get('stages',[])},
            source='Historical completion summary, not revalidated or reused as current PASS'))
    write(out/'prior_formal_attempts.json',formal)
    logs=files_manifest(list(Path(ctx['work']).rglob('*.log')))
    write(out/'log_manifest.json',dict(run_id=ctx['run_id'],logs=logs))
    baseline=ROOT/'work/round3_prechange'
    shutil.copy2(baseline/'baseline.json',out/'baseline.json')
    write(out/'constructed_expected_values.json',dict(kind='CONSTRUCTED_STATISTICS_ONLY',cases=[
        dict(name='overlap_touch_contain_duplicate_crosssat',window=[0,10],points=['p','z'],
             intervals=[[8,9],[3,5],[1,4],[2,3],[5,6],[1,4]],expected_merged=[[1,6],[8,9]],expected_gap_s=2,coverage=.5),
        dict(name='clipping',intervals=[[-2,1],[8,12],[12,14]],window=[0,10],expected_merged=[[0,1],[8,10]],gap_s=7),
        dict(name='pooled_gaps',point_a_gaps=[1,3],point_b_gaps=[9],point_c_gaps=[],
             expected_gap_count=3,expected_mean_s=13/3,expected_max_s=9,insufficient_fraction=1/3),
        dict(name='no_observed_gaps',expected_mean=None,expected_max=None,reason='NO_OBSERVED_INTERNAL_GAPS')]))
    with zipfile.ZipFile(out/'code_snapshot.zip','w',zipfile.ZIP_DEFLATED) as z:
        for f in read(out/'code_manifest.json')['files']:
            z.write(f['path'],Path(f['path']).relative_to(ROOT))
    grid=read(out/'grid/frozen.json')
    bpath=out/'B/cases/matching.csv'
    bsummary=read(out/'B/cases/experiment.json') if (out/'B/cases/experiment.json').exists() else {}
    bmetrics={}
    if (out/'B/cases/search_results.json').exists():
        searches=read(out/'B/cases/search_results.json')
        matches=[m for s in searches for m in s['comparison']['matches']]
        bmetrics=dict(searches=len(searches),reference_events=sum(s['comparison']['reference_count'] for s in searches),
            detected_events=sum(s['event_count'] for s in searches),matched=len(matches),
            missed=sum(len(s['comparison']['missed']) for s in searches),extra=sum(len(s['comparison']['extra']) for s in searches),
            max_start_error_s=max([abs(m['start_error_s']) for m in matches],default=None),
            max_stop_error_s=max([abs(m['stop_error_s']) for m in matches],default=None),
            max_duration_error_s=max([abs(m['duration_error_s']) for m in matches],default=None),
            clipped_matches=sum(m['clipped_start'] or m['clipped_stop'] for m in matches))
        write(out/'event_validation_summary.json',bmetrics)
    cpaths=sorted(out.glob('C/grid*/experiment.json'))
    cdetails=[dict(folder=str(p.parent),**read(p)) for p in cpaths]
    stages='\n'.join(f'|{s}|{rows[s]["status"]}|' for s in ['data','environment','generate','tests','A','B','C'])
    lifetable='\n'.join(f'|{x["task"]}|{x["status"]}|{x["cleanup"]}|{x.get("worker_exit_code")}|{x["elapsed_s"]:.3f}|{x["memory_peak_working_set_bytes"]/1024**2:.1f}|' for x in life)
    failed='\n'.join(f'- {s}: '+ '; '.join(c.get('reason',c['id']) for c in rows[s]['checks'] if c['status']!='PASS') for s in ['A','B','C'] if rows[s]['status']!='PASS') or '- A/B/C核心检查没有失败。'
    ctext='\n'.join(f'- {x["duration_s"]}s、{x["points"]}点、1星：{x["event_count"]}个真实事件；计算{x["elapsed_s"]:.3f}s，轨迹/变换接口{x["state_provider_calls"]}次，Sensor Access {x["access_calls"]}次。' for x in cdetails) or '- 真实网格阶段没有完成，不声明通过。'
    text=f'''# 第三轮开发与验证审计

唯一运行 `{ctx['run_id']}`，解释器 `{sys.executable}`。核心验收 **{summary['core_acceptance']}**，退出码 **{summary['exit_code']}**。A、B、C独立验收，下游受阻不算PASS。该目录中当前运行证据与development_attempts中的开发尝试分开，不跨运行拼接。

|阶段|状态|
|---|---|
{stages}

## 实际完成和证据

修改前17项回归实际运行，退出0，详见baseline.json及`{baseline}`日志。正式回归按tests/result.json逐项记录，包含原机制测试、构造统计、匹配和裁剪反例。配置、输入、代码绑定见run.json、config.json和三个manifest；阶段产物、实际命令退出码、耗时均有哈希绑定。运行前后原始资料及前两轮有效产物未变：{summary['source_preserved']}；执行期间代码未变：{summary['code_unchanged']}。

检查清单与容差见checklist.md，假设版本 `{cfg['assumptions_version']}`。本轮没有优化搜索，没有28星全区7天计算。28星参数生成仅用作既有基线，本轮真实STK仅1星。

## A：运行可靠性

监督器从本轮确切工作进程HANDLE复制实例HANDLE，核对进程身份、创建时间、映像路径及新空场景后握手；不是重新查PID清理。工作进程释放局部COM、CloseScenario、Quit、释放app，接口计数记录在worker.json；超时后监督器仍能使用自身有效句柄回收。无所有权时禁止清理并BLOCKED。未按名称批量终止。

|任务|状态|退出方式|工作进程退出码|总耗时s|STK峰值内存MiB|
|---|---|---|---:|---:|---:|
{lifetable}

故障注入只作用于本轮实例。timeout的非零工作进程退出码是预设注入结果，必须有FORCED_RECOVERY和无残留才通过机制验收；不描述为正常退出。其他任务如使用强制回收，正常路径验收失败。实例前后身份列表见lifecycle_index.json。监督器本身被外部强杀的情形未覆盖。

保存工程在A/save/project/Round3.sc，新独立实例重开并重算的工程在A/reopen/project/Round3.sc。parameters.json记录初始轨道、历元、时段、J2/30s、姿态轴、传感器安装/半角和诊断点地形零高；parameter_comparison.json逐值比较。访问显式RemoveAccess后重新取得对象再ComputeAccess，重开不使用旧缓存；access_comparison.json比较正负案例，保留第二轮中细档20μs收敛要求。保存/重开不是仅数对象。

## B：窄视场验证范围

16组DIAGNOSTIC案例包含不同纬度、升降、相位、交叉迹边缘、极短事件、两组可见但视场外负例、非整数中心，以及起止裁剪。每组3种长窗口/采样相位，共48次搜索；实际数量以B/cases的文件为准，未完成时不预填通过。参考使用独立角度求根，实际搜索只接收目标与长窗口，粗步长5/2/1s自主寻找沿迹过零候选，然后仅局部1ms搜索真实Sensor Access。

参考与实际共用STK轨迹和地固坐标变换，不能视为独立传播器或全局无漏检证明。参考有效范围是构造种子附近的局部事件；额外事件必须保留在匹配表。v2参考使用保护割线并验证最终1e-9s符号括区；粗筛批量Fixed位置/速度加omega×r近似只生成候选，首尾轴差门限1e-5，候选再由精确状态重新包围和精化。匹配按开始/结束均≤0.0002s一对一配对；中细对齐差≤0.00002s。窗口先裁剪参考，裁剪边界单独标记。精化缓存删除、窗口、设置读回、耗时和候选全部见search_results.json；完整表见matching.csv。极端沿迹切触、没有中心过零的侧边事件、任意姿态及全局长时搜索不在已验证范围。

本运行B结果：`{bsummary}`。

实际匹配汇总：`{bmetrics}`。这里参考事件计数按每次搜索窗口重复计数，不能当作不同物理事件总数。

## C：固定网格与统计

边界保留{grid['component_count']}个多边形。完整冻结0.1°网格{grid['full_grid_count']}点，按预设等秩规则选16点，点集SHA256 `{grid['point_set_sha256']}`。各组成部分完整网格点数{grid['component_grid_counts']}，子集点数{grid['component_subset_counts']}；未被子集选中的岛屿是覆盖缺口，未为取得事件增补点。GeoJSON lon/lat平面covers含边界，WGS84及与STK GreatArc边差异继续列为临时解释。

{ctext}

逐星原始事件、逐点查询（包括零事件）、配置/点集关联保留；merged_intervals.json含来源record_id，statistics.json保留所有16点和固定分母。真实抽查见C/real_point_handcheck.json。全零时不移动点、不删除未覆盖点，真实正事件聚合链路标记NOT_DEMONSTRATED，构造测试不替代真实支持。

统计默认容差0合并重叠/相接；不足两次访问点仍在总体状态中。内部空窗按观测到的间隔总数汇总，不平均各点平均值；无有效空窗平均/最大为null，未达到覆盖阈值为null并记录原因；首尾空窗另列。没有访问时边界空窗未知，另记录整个窗口未覆盖时长。这些都是小样本工程条件统计，不是官方T90、T100、Tavg、Tmax或得分。

构造期望值见constructed_expected_values.json及tests/test_round3_statistics.py，覆盖重叠、相接、包含、重复、乱序、跨星、裁剪、非法/非有限、0/1/2次访问和不等空窗数权重；其证据类型为CONSTRUCTED_STATISTICS_ONLY。

## 失败、限制及下一步

{failed}

原始AT中文描述损坏继续为非阻塞的数据质量FAIL，数值几何单独审计，源文件没有修复。UTCG、地球常数、FOV半角、惯性沿迹及源边界基准均为工程假设。第二轮有效运行的强制终止和重开仅数量检查属于历史事实，未改写历史记录。

下一步规模建议需以本轮实际C耗时决定，不能用已包围0.1s的耗时外推。若C真实任务完成，可先在同一16点集上扩展至2星、6000s，再以实测扩至4星、16点、12000s；先约2倍/4倍串行计算成本另加每实例启动退出开销，这是待测预算估算而非实测全量性能。未取得真实正事件时优先检验固定轨道与区域的机会窗口，不能调整点位追求正数；依赖A/B失败则先修复失败项后重跑新run_id。

## 复现、日志和手动提交

完整日志位置：`{ctx['work']}`；精确argv及返回码见commands和invocation.json。log_manifest.json提供日志哈希。正式复现命令见reproduction.md，输出必须新目录；本运行最终状态见summary.json。code_snapshot.zip对应运行代码，可用于离线核对，源代码仍在G:\\fmb666。

建议手动审阅并提交README.md、.gitignore、requirements-smoke.txt、configs、src、tests、tools、docs（含前两轮尚未跟踪的依赖）。本轮新增主要是src/round3、configs/round3.json、tests/test_round3_*.py、tools/run_round3.py、tools/probe_round3_lifecycle.py、tools/finalize_round3.py及docs/round3*.md。data、work、缓存、原ZIP/DOCX、此交付目录及STK工程不进入普通提交。

```powershell
Set-Location G:\\fmb666
git status --short
git diff --check
git diff -- README.md
git add .gitignore README.md requirements-smoke.txt configs src tests tools docs
git commit -m "feat: add supervised STK validation and fixed-grid engineering statistics"
```

以上命令仅供用户手动执行；本轮没有add、commit、push或PR，没有修改远程仓库。

Windows句柄依据：[Microsoft DuplicateHandle](https://learn.microsoft.com/windows/win32/api/handleapi/nf-handleapi-duplicatehandle)。STK使用本机11.6官方接口文档，路径和SHA256见official_interfaces.json。
'''
    (out/'audit_report.md').write_text(text,encoding='utf-8')
    summary['report_generated']=True
    write(out/'summary.json',summary)
    write(out/'delivery_verification.json',dict(run_id=ctx['run_id'],core_acceptance=summary['core_acceptance'],
        source_preserved=summary['source_preserved'],code_unchanged=summary['code_unchanged'],
        stages={s:rows[s]['status'] for s in rows},exit_code=summary['exit_code']))
    write(out/'output_manifest.json',dict(run_id=ctx['run_id'],files=artifacts(out)))
    print('REPORT',out/'audit_report.md','EXIT',summary['exit_code'])
    return summary['exit_code']

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',required=True)
    raise SystemExit(finalize(p.parse_args().output))
