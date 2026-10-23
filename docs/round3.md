# 第三轮运行与复现

本轮按 `docs/round3_checklist.md` 执行。正式入口是 `tools/run_round3.py`，使用 `D:\conda_envs\fmb\python.exe`。不得把历史第二轮 PASS、开发单任务 PASS 或构造统计测试当作本轮完整验收。

```powershell
Set-Location G:\fmb666
$env:PYTHONIOENCODING = 'utf-8'
$round3Output = 'G:\gpt-file\Codex\outputs\bd-fmb\round3_' + [guid]::NewGuid().ToString('N')
& 'D:\conda_envs\fmb\python.exe' tools\run_round3.py --output $round3Output
$LASTEXITCODE
```

需要可创建独立桌面 COM 服务器的 Windows 权限。串行启动 STK11.Application，禁止使用 GetActiveObject 或按名称结束进程。工作进程在新空实例校验后把有效句柄复制到监督器，收到握手确认才执行模型任务。正常退出释放实验局部 COM 引用、CloseScenario、Quit、释放根对象和应用；监督器等待真实内核句柄，必要时仅强制回收该句柄所指本轮实例。顶层监督器本身被外部强杀的情形不在当前保障范围。

窗口以 EpSec 记录，历元暂用 UTCG 2027-01-01。固定 NadirECIVel、单位四元数安装、沿迹0.001622°及交叉迹0.81°半角，椭球零高地形关闭，传播30s步长。临时假设没有赛事确认。

实际搜索仅接收目标坐标、长窗口和采样设置。粗筛沿迹有符号分量过零，固定0.1s端点补边；只对自主发现候选±0.05s进行真实 Sensor Access 精化，保留RemoveAccess、重新取得对象、设置读回及计算记录。该方法适用于已测近地圆轨道及固定姿态，尚不覆盖任意姿态、沿迹切触/不经过中心的极端侧边事件。参考是已知构造诊断点的独立角度二分；共享STK轨迹/变换，局部范围不能推导为长时间全局真值。

16组诊断案例与16个正式调试子集网格点分开。网格来自全部原多多边形（含岛屿），0.1°锚点(0,0)，平面lon/lat covers包含边界，按纬度经度排序等秩抽取。完整网格只导出冻结表，不做全部访问。选中子集可能没有岛屿点，按证据列明；不补点或改分母。GeoJSON直线边与STK GreatArc连续边不相同，源数据基准仍为WGS84假设。

统计默认精确合并重叠和相接区间（容差0），保留原始record_id溯源。平均空窗按全部观测内部空窗数量汇总，未覆盖和不足两次访问点保留。首尾空窗另列，无有效空窗及未达覆盖阈值为null并说明原因。本轮输出均为小样本工程指标，不是官方T90/T100/Tavg/Tmax。

失败运行必须保留，修复后新建输出目录。`--stop-after A`或`B`用于有明确标签的开发复测，下游BLOCKED，完整验收退出码1。`tools/probe_round3_lifecycle.py`只做开发单任务验证，不能替代正式入口。

## 本轮开发记录

- 修改前17项回归实际通过，work/round3_prechange保存日志和原代码快照。
- dev01：在CurrentScenario查询后Isolate导致STK拒绝；移至创建根对象后立即调用。该实例正常退出，尝试FAIL。
- dev02：正常业务已结束但管道poll的BrokenPipe未处理，监督器报告FAIL。扩展EOF处理到poll和recv两处；未伪造PASS。
- dev03：STK半长轴SizeShape枚举原误用1（MeanMotion），官方11.x文档确认4（SemimajorAxis），修复。
- dev04：RemoveAccess后尚未计算时读取ComputedAccessIntervalTimes触发STK 11“Invalid dimension name”。改为显式删除、释放和重新取得访问对象，再ComputeAccess及读回结果。
- dev05：独立工程保存、真实正负访问及正常退出实测通过；这只是开发单任务证据。
- 正式尝试20261023T094657_62bf522973：冻结网格误假设输入为FeatureCollection，真实源为单个Feature。启动STK前失败，下游BLOCKED；补上格式分支及真实源边界冻结回归，原失败目录保留。
- 正式尝试20261023T095005_1464f49ada：A全部通过，B首组3种长窗口搜索均匹配，但首组77.600s，估算16组1361.595s超过600s阶段预算，B停止并FAIL、C BLOCKED。改批量轨迹粗筛和保护割线精化；不扩大预算、不改事件容差，下一完整运行重新执行A，旧A不能拼入新运行。

v2粗筛采用STK批量Fixed位置/速度及v_fixed+omega×r近似惯性速度，只用于发现候选。轴差首尾校验门限1e-5，约600km距离对应6m/约1ms，小于候选50ms半窗；精确J2000/VGT轨迹对候选重新包围并求根，最终真实Sensor Access未改变。参考角度根采用保护割线并核验1e-9s终括区。该性能修复与原物理模型、0.0002s事件及0.00002s收敛容差分开记录。

失败尝试目录及run_id由交付报告逐一索引，以上任何成功子项不计入最终运行验收。原始AT中文损坏继续非阻塞FAIL，不改源文件。

## 手动提交

本轮只提供命令供用户自行执行。先检查此前未跟踪文件，保留前两轮实现依赖。

```powershell
git status --short
git diff --check
git diff -- README.md
git add .gitignore README.md requirements-smoke.txt configs src tests tools docs
git commit -m "feat: add supervised STK validation and fixed-grid engineering statistics"
```

data、work、输出目录、缓存、原ZIP/DOCX、STK工程不进入普通提交。原资料与大工程单独归档。本轮不执行add、commit、push，不创建PR。

接口依据：[Microsoft DuplicateHandle](https://learn.microsoft.com/windows/win32/api/handleapi/nf-handleapi-duplicatehandle)；安装版STK11 Help/Programming/Content/DocX中的IAgStkAccess.RemoveAccess、IAgVePropagatorJ2Perturbation、AgEClassicalSizeShape和IAgUiApplication.Quit，交付保存文档路径及哈希。
