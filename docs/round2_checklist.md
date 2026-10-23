# 第二轮检查清单与预先声明的验收条件

本清单在第二轮代码实现和 STK 诊断前建立。使用第一轮 600 km、28/7/1 基线，只创建一颗诊断卫星；不做正式覆盖评估。

|预期行为|检查方法|证据|验收条件|
|---|---|---|---|
|保留原资料和第一轮结果|运行前后 SHA-256 对照|input_manifest、preservation|零改动|
|独立检查不依赖 STK 安装|受控 missing/error 探测、真实子进程 --skip-stk|机制测试、环境阶段 JSON|数据/生成 PASS；STK 明确 SKIPPED；探测错误结构化|
|不混入旧 PASS|非空输出拒绝、改 run_id/config/input/code 哈希、缺失/坏 JSON、未完成结果|机制测试 JSON、阶段封套|均拒绝 PASS；必需阶段失败验收退出非零|
|超时不污染后续结果|真实短时 sleep 子进程、超时杀死该子进程|命令日志与阶段结果|timeout FAIL，下游 BLOCKED，独立阶段继续|
|统一配置|生成器、STK、独立几何与报告读取同一 run config|配置哈希、STK 读回|配置一致；未支持的配置显式拒绝|
|姿态和坐标关系正确|真实 J2000 r/v、Fixed r、STK VGT 旋转及 Body/Sensor 轴读回；独立叉乘构建基|trajectory、geometry JSON|位置变换误差 ≤1e-6 km；正交误差 ≤1e-10；轴单位向量差 ≤1e-6（约0.2角秒）|
|地心指向/椭球投影正确|射线交 WGS84 椭球，独立经纬度逆变换，再读回 Facility ECEF|诊断点 CSV、投影图|地面点往返位置误差 ≤1e-6 km；入射与法线区别单独记录|
|窄/宽边方向正确|沿迹与交叉迹半角的0.5/1.5倍射线投影；真实 Sensor Access|几何判定与区间 CSV|中心点正事件；交叉迹1.5倍点可见但短窗零事件；偏移方向与独立角度一致，角误差 ≤1e-5 deg|
|正事件导出真实可核查|读取 Access Data，再读 CSV|access CSV、原始结果 JSON|开始<结束、有限、在窗内；CSV与原始访问记录一致；空查询明确有 event_count=0|
|事件搜索收敛|先独立包围事件；同一过境短窗三档最大步长0.01/0.005/0.001s，收敛1e-4/1e-6/1e-8s|convergence.csv|三档事件数一致且为1；最后两档边界/持续时间差≤2e-5s|
|独立事件核验|真实轨迹插值点 + ECEF目标 + 预期姿态，二分角度边界；不调用Access|independent_events JSON|与精细STK开始/结束差≤2e-4s；相当于约1.5m地面运动，仅短窗数值几何校验，不是微秒物理精度证明|
|工程/进程隔离|DispatchEx、PID不在之前列表、空场景；保存重开|ownership、工程、cleanup|不触碰旧实例；清理结果明确记录|
|报告与验收分离|完整阶段封套先落盘，再汇总|summary.json|report_generated、execution_completed、core_acceptance独立；必需失败/受阻/超时验收非零|

球面 2h*tan(alpha) 只作幅宽量级对照，不能当作椭球精确基准。原 AT 中文损坏继续为数据质量 FAIL，独立于本轮核心机制/几何验收。时间系统、常数与半角解释保持版本化工程假设；不因结果改变容差。
