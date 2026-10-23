# 第二轮复现与验收

唯一推荐入口是 `tools/run_round2.py`。兼容入口 `tools/run_smoke.py` 已转向相同实现。`tools/finalize_report.py` 仅接受绑定完整的 schema-v2 目录，并返回核心业务验收状态；第一轮旧目录拒绝处理，不会修改。旧 `stk_smoke.py`、`sensor_smoke.py`、`run_logged.py` 是第一轮历史实现，不再用于第二轮执行。

```powershell
Set-Location G:\fmb666
$fmbPython = 'D:\conda_envs\fmb\python.exe'
$env:PYTHONIOENCODING = 'utf-8'
$round2Out = 'G:\gpt-file\Codex\outputs\bd-fmb\round2_' + [guid]::NewGuid().ToString('N')
& $fmbPython tools\run_round2.py --output $round2Out
$LASTEXITCODE
```

输出目录必须为空/不存在。每次运行生成唯一 run_id，配置、输入、代码、命令、阶段结果和产物使用 SHA-256 绑定；不能复用旧 PASS 或将不同运行最后一个同名检查拼接。退出 0 表示所有必需阶段通过，失败/受阻/超时返回 1，入口错误/非空目录返回 2。summary.json 分开显示 report_generated、execution_completed、core_acceptance。原 AT 文本质量 FAIL 是可见的非核心缺陷，不能消除或冒充通过。

无 STK 可用 `--skip-stk`，此时不读取注册表、不激活 COM，数据、生成和机制测试仍执行。正常探测失败结构化保存，STK 阻塞，独立检查继续。STK 通过 ProgID 注册表定位，亦可在配置指定可执行文件；相关模型/官方文档从探测出的安装根读取，不固定 G 盘安装。全部使用现有 fmb，不升级依赖。

统一配置为 configs/round2.json；历元、时间系统、参考系、动力学常数、平均高度半径、J2参考半径、椭球、步长、视场、安装姿态、访问精度、验收容差和工程假设均在其中。诊断只支持已明确的 UTCG/J2000、NadirECIVel、单位安装四元数、椭球零高程；不支持的解释明确拒绝。

STK预算每任务180秒，其他阶段120秒。机制测试中超时探针0.2秒。仅用DispatchEx获取新PID且确认空白场景；Quit后若仍存活，使用从创建时保留的原进程句柄清理（不根据未知PID查找或强杀）。外层超时可能留下已登记的独立实例，必须查看stk_timeout_cleanup.json；未取得可信身份的进程不清理。第一轮工程从不载入后覆盖。

## 机制反例

```powershell
# 仅受控机制测试：标记 mechanism_test=true，不属于实际 STK 验收
& $fmbPython tools\run_round2.py --output "$round2Out-missing" --mechanism-test --probe-fault missing --skip-stk
& $fmbPython tools\run_round2.py --output "$round2Out-error" --mechanism-test --probe-fault error
& $fmbPython tools\run_round2.py --output "$round2Out-timeout" --mechanism-test --stage-fault generate:timeout
& $fmbPython -m unittest discover -s tests -v
```

分别预期退出0、1、1。后两者独立数据仍通过；STK为BLOCKED。测试还真实执行坏JSON、缺失结果、子进程超时、旧身份、坏产物、无命令证据和非零退出码反例。正式运行将机制案例完整结果保存在 tests/cases，日志在项目 work/<run_id>；不会并入真实STK的PASS。

## 几何验收口径

先读取真实J2000位置/速度及Fixed位置，检验坐标旋转方向。用径向负方向作Z、速度在垂直Z平面的投影作X、Z叉X作Y；再比较STK Body和Sensor轴读回。以射线和WGS84椭球交点构造中心、沿迹/交叉迹内外偏移点。所有点为DIAGNOSTIC_ONLY，不进入正式区域评价。

固定地面点必须显式关闭UseTerrain；否则STK默认可能忽略AssignGeodetic的0高度，用真实地形替换成数公里海拔，导致独立椭球模型失配。本轮只修改新建诊断Facility的此属性；不修改安装、地形数据或原始区域。

三档搜索在同一个被独立角度函数包围的短窗内比较。正事件必须真实导出Access Data、CSV读回一致；交叉迹外侧点需几何可见且零事件。独立二分不调用Access；共享轨迹和坐标旋转不等于独立传播器，因此结论只涉及当前STK轨迹下的成像和事件几何。地心指向和椭球法线不同，球面幅宽量级比较不用于微秒边界验收。

所有容差在 docs/round2_checklist.md 中预先记录。全部通过后可开始小规模固定网格指标开发，仍不能宣称正式四指标、全球访问完备性或优化提升。

## 手动提交

```powershell
git status --short
git diff --check
git add .gitignore README.md requirements-smoke.txt configs src tests tools docs
git commit -m "fix: bind validation evidence to runs and verify STK sensor geometry"
```

以上供用户手动执行。本轮不add/commit/push/PR。原始ZIP/DOCX、data、work、缓存、STK工程与完整运行输出不直接加入普通提交。
