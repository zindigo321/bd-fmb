# 第一轮冒烟复现

本轮仅验证原始数据读取、Walker-Delta 参数生成与校验、STK 11.6 短时传播、保存重开和可选 Sensor Access。未实现优化搜索和全网格性能指标。原始包中的 Python 脚本没有运行。

## 环境与运行

已核实解释器为 `D:\conda_envs\fmb\python.exe`，Python 3.11.17、64 位。没有使用 base。生成器只需标准库；STK 使用已存在的 pywin32 312，边界核验与绘图补充 Shapely 和 Matplotlib。实际全部依赖版本在输出 `environment.json` 中。

在 PowerShell 执行，使用新输出目录保留本次证据：

```powershell
Set-Location G:\fmb666
$fmbPython = 'D:\conda_envs\fmb\python.exe'
$env:PYTHONIOENCODING = 'utf-8'
$smokeOut = 'G:\gpt-file\Codex\outputs\bd-fmb\smoke_round1_manual01'
& $fmbPython tools\run_smoke.py --output $smokeOut
# 可选真实相机访问，核心 STK 工程成功后执行
& $fmbPython tools\run_logged.py --output $smokeOut --id sensor_worker --timeout 180 tools\sensor_smoke.py --output $smokeOut
& $fmbPython tools\finalize_report.py --output $smokeOut
```

无 STK 时可用 `--skip-stk`；这不代表 STK 验收通过。核心 STK 子进程墙钟超时 240 秒，可选传感器 180 秒，其余任务 120 秒。超时保留证据，可能留下本轮独立 STK 实例；不强杀未知或用户进程。完整日志在 `work/<运行标识>/<尝试时间>/`；交付目录保存命令、状态、CSV、图、报告及工程。

单独生成和测试：

```powershell
& $fmbPython -m src.cli generate --n 28 --planes 7 --phase-factor 1 --h-km 600 --output "$smokeOut\orbits.csv"
& $fmbPython -m unittest discover -s tests -v
```

支持正式范围 `2 <= N <= 100` 中满足 `N % planes == 0` 的等星数 Walker-Delta。显式输入 `planes` 和 `phase-factor`，默认是 28/7/1。非整除面分配、不等星数构型和 N=1 明确报错。1 星 STK 检查只从基线选取首星作诊断，不能当作正式合法输入。正常输入测试覆盖 2、7、28、30、100，未证明任意 N 的自动选型或最优性。

## 模型约定

基线 N=28、P=7、每面 4 星、F=1、600 km（调试值）、e=0，ω=0，ν=u。UTCG 暂定历元 2027-01-01 00:00:00，J2000 初值；只传播到 02:00:00。传播步长与位置报表采样均为 30 秒，但含义不同。

高度严格通过 `a = R_mean + h` 转换。暂定 `R_mean=6371.0088 km`；J2 公式中的参考半径单独为 `R_J2=6378.137 km`，不能拿来定义高度。`mu=398600.4418 km³/s²`、`J2=1.08262668e-3`、回归年 `365.2421897 day`；均为显式调试常数，非已确认的主办方常数。

`Ωdot = -(3/2) J2 sqrt(mu/a³) [R_J2/(a(1-e²))]² cos(i)`，目标 `+2π/(365.2421897*86400) rad/s`。反求 `i` 后以同一关系检查绝对残差 `<=1e-15 rad/s`。这是一级长期平均节点进动约束；不等于验证了 STK 多日太阳同步漂移。

STK Connect `SetState ... Classical J2Perturbation ... J2000 ...` 使用半长轴，最后一个参数为平近点角；本轮 e=0、ω=0，所以 M=ν=u。读取 J2000 Classical Elements 核对 a/e/i/Ω/u。STK 保存内部根数可能转换到 ICRF；不能把保存文件里的 ICRF 角直接与 J2000 输入比较。

STK 安装默认 Earth.cb/EGM2008.grv 的常数与本轮生成器暂定常数略有差别；见交付 model_environment.json。没有修改安装文件或重力模型；下一轮需统一常数和平均/瞬时根数口径。

传感器仅作可选连通检查：Sat001 的 +Z 指向地心，+X 受 ECI 速度方向约束，传感器采用单位四元数安装。暂取沿迹/交叉迹半角 0.001622°/0.81°，分别对应 XZ/YZ。此解释与幅宽近似一致，但仍待赛题确认；没有为获得访问扩大视场。访问最大采样间隔 0.01 秒、最小 0.001 秒、事件收敛 1e-5 秒，开启精确事件求解；没有完成漏检/收敛证明。安装版本的 OM MinTimeStep 实际下限为 0.001 秒，比 Connect AccessConfig 帮助中全局最小值更大；以实际接口验证为准。

## 数据和范围

数据提取仅限题目所需嵌套 ZIP，保留原包。GeoJSON 经纬度及多多边形、孔洞信息原样审计；不做矩形/凸包替换或自动修复。四省 union 对比和 AT 顶点对比不能确认主办方是否采用该行政范围、简化精度或插值方式。AT 使用 GreatArc 边界，而 GeoJSON 的平面连线不是严格同一条测地曲线，顶点一致不代表连续边界逐点完全一致。

立项书是开运联合观测任务规划，其 TLE、站点排程、优先级、故障重规划与历史成绩不进入本轮。

## 官方接口依据

以安装配套 `G:\Help\Programming` 的 STK Objects 11 / Connect 文档为准。具体文件及 SHA-256 在输出 `official_interface_sources.json`。

- `cmd_SetStateClassical.htm`：J2Perturbation、J2000、参数顺序及步长。
- `AgUiApplicationLib~IAgUiApplication.html`：ProcessID、Personality2、Quit。
- `STKObjects~IAgStkObjectRoot~Isolate.html`：根对象单位隔离。
- `cmd_DefineSensor.htm`：Rectangular 参数顺序为 VerticalHalfAngle、HorizontalHalfAngle。
- `STKObjects~IAgSnRectangularPattern.html`：Horizontal 对应 XZ；Vertical 对应 YZ。
- `cmd_PointFixed.htm`、`cmd_SetAttitudeProfile.htm`、`G:\Help\Content\stk\referenceframesvehicle.htm`：安装姿态与地心指向。
- `STKObjects~IAgStkAccessAdvanced.html`：访问搜索及事件精度。

## 手动提交

建议提交 `.gitignore`、`README.md`、`requirements-smoke.txt`、`src/`、`tests/`、`tools/`、`docs/`。先审阅：

```powershell
git status --short
git diff -- README.md
git add .gitignore README.md requirements-smoke.txt src tests tools docs
git commit -m "feat: add reproducible round-one constellation and STK smoke checks"
```

以上 add/commit 仅供手动执行。此次没有执行 add、commit、push 或创建 PR。不要把原始 ZIP/DOCX、data、work、缓存、日志、大体积 STK 工程直接放入普通提交；工程留在交付目录，必要时另用 LFS 或团队附件存储。
