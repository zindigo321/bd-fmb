from pathlib import Path
from .evidence import write,read

def render(ctx,summary):
    out=Path(ctx['output']);cfg=read(out/'config.json')['config']
    failed=[c for r in summary['stages'] for c in r['checks'] if c['status'] in ['FAIL','BLOCKED']]
    lines=['# 第二轮开发与验证报告','',f"运行 `{ctx['run_id']}`；机制测试标记：{ctx['mechanism_test']}。",'',f"核心验收：**{summary['core_acceptance']}**；整体执行已完成：{summary['execution_completed']}；验收退出码：{summary['exit_code']}。报告成功生成不等于业务通过。",'',
           '## 状态与证据','', '|阶段|状态|必需|','|---|---|---|']
    for stage in summary['stages']:lines.append(f"|{stage['stage']}|{stage['status']}|{stage['required']}|")
    lines+=['','全部检查详见 summary.json，各阶段 result.json 绑定运行、配置、输入与代码哈希。未完成、损坏或身份不符的结果不得通过。','',
            '## 未解决问题','']+[f"- {c['id']}: {c.get('reason',c)}" for c in failed]
    lines+=['','原始 AT 中文描述损坏属于数据质量 FAIL；数值几何单独验收，不修改源文件。UTCG、地球常数、视场半角口径仍为工程假设，未获赛事确认。','',
            '## 审计修复','',
            '原实现确认存在：skip-stk 提前读注册表、非空输出混用、按文件存在拼接历史结果、跨模块配置硬编码、报告成功退出与业务验收混淆。新的唯一入口拒绝非空目录；独立环境阶段捕获探测错误；阶段先落盘，再按 run_id 和配置/输入/代码哈希、命令退出码及产物哈希汇总。旧入口转向本轮入口，旧无绑定报告拒绝处理。机制故障注入单独标记。','',
            '开发尝试曾发现Facility默认UseTerrain=true导致约2km地面高度偏差及约0.96ms独立事件误差。按本机官方接口文档关闭新建诊断点的自动地形高度，保持椭球零高程；没有改视场或放宽容差。历史尝试详见项目docs/round2_development_notes.md，其结果不计为本次PASS。','',
            '## 几何与收敛','',
            '使用28/7/1、600 km基线生成全部轨道表，STK只创建Sat001开展短诊断。诊断点由真实轨迹、预期姿态与WGS84椭球相交生成，不属于正式区域点集。沿迹/交叉迹半角和安装姿态见config.json；不扩大视场。','',
            '独立几何从真实J2000位置/速度计算地心指向和速度约束基，使用经位置验证的STK坐标转换表达为ECEF；目标方向与相机轴读回比较。独立角度边界二分不调用Access。椭球交点与经纬度往返核对；球面幅宽只作量级参考，地心指向不等于椭球法线。','',
            '预先容差见 docs/round2_checklist.md 和配置：坐标误差1e-6 km；轴单位向量差1e-6；最后两档开始/结束/持续差≤2e-5s；独立边界误差≤2e-4s。容差没有根据运行结果放宽。','']
    stk=out/'stk'
    if (stk/'convergence.csv').exists():lines+=['```csv',(stk/'convergence.csv').read_text().strip(),'```','']
    if (stk/'footprint.json').exists():lines+=['椭球投影：','```json', (stk/'footprint.json').read_text(encoding='utf-8'),'```','', '![诊断几何](stk/geometry_projection.png)','']
    lines+=['## 复现','', '```powershell', 'Set-Location G:\fmb666', "& 'D:\conda_envs\fmb\python.exe' tools\run_round2.py --output ('G:\gpt-file\Codex\outputs\bd-fmb\round2_' + [guid]::NewGuid().ToString('N'))", '```','',
            f"完整 stdout/stderr 日志在 `{ctx['work']}`，commands/*.json 记录实际命令、退出码、超时及耗时。所有Python任务使用fmb解释器，无环境升级。历史开发尝试保存在各自独立运行目录，不拼接为本轮PASS。",'',
            '本轮不计算优化、7天全网格或T90/T100/Tavg/Tmax。核心验收通过仅意味着可进入小规模固定网格开发；正式指标验收仍需确认规则并扩展代表性过境/纬度、网格事件完备性和成本验证。若核心未通过，先解决失败项。','',
            '## 手动提交','',
            '建议审阅提交 README.md、.gitignore、requirements-smoke.txt、configs/、src/、tests/、tools/、docs/。源ZIP/DOCX、data/、work/、STK工程及运行产物不进入普通提交。此次未执行add/commit/push/PR。','',
            '```powershell','git status --short','git diff --check','git add .gitignore README.md requirements-smoke.txt configs src tests tools docs','git commit -m "fix: bind validation evidence to runs and verify STK sensor geometry"','```']
    (out/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    summary['report_generated']=True;summary['unresolved_checks']=failed
    write(out/'summary.json',summary)
