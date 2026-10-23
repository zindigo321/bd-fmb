# 第四轮执行及复现

源码使用 UTF-8。只用明确编码的文件传递中文；禁止将含中文 Python 源通过 PowerShell 管道输入解释器。报告关键句逐字校验，同时检查 U+FFFD 和连续问号。

```powershell
Set-Location G:\fmb666
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONIOENCODING='utf-8'
& D:\conda_envs\fmb\python.exe -m unittest discover -s tests -v
& D:\conda_envs\fmb\python.exe tools/run_round4.py --output "G:\gpt-file\Codex\outputs\bd-fmb\round4\fresh_unique_directory"
```

输出目录必须全新且为空。运行需要本机 STK11 COM 权限。每个实例采用独立工作进程、确切句柄复制监督，禁止清理未知实例。主监督器不得被外部强杀；每个任务的超时通过监督器有界回收，日志在 work 下。实例资源释放路径沿用第三轮并于本轮复测。

配置 `configs/round4.json` 声明来源、冻结点集、2/4/7面首星选择、时间档位、筛查参数及预算。28星基线仍按28星生成，不把所选2星重生成2星构型。12000秒与86400秒档位分别记录，24小时内各星数使用相同窗口。任何运行中代码修改均需新目录及新 run_id 重测；失败产物保留。

`summary.json` 分别保存可信阶段证据、命令退出/超时、业务检查。报告生成只设置 `report_summary.json` 的报告状态，不改变业务。H1真实正事件、H2至少两星真实贡献、H3真实内部空窗分别验收；其中必需项目未达成，顶层退出1。原始AT文字问题是非阻塞FAIL，原始文件不修改。

搜索采用批量5秒状态、保守地平线筛选、沿迹过零、精确轨迹重括及交叉迹2倍半角门，再进行局部真实 Sensor Access。毫秒步长只用于候选局部0.1秒窗口。完整窗口的空窗统计以该搜索所观测事件为依据；沿迹擦边、多根夹在采样单元等未证完备，不能宣称官方或全局指标。16点不代表完整3264点或岛屿，地面继续椭球零高/禁用地形。

建议用户手动审阅提交：README.md、.gitignore、requirements-smoke.txt，以及 configs、src、tests、tools、docs 中的源码和文档（前几轮仍未跟踪的依赖也需要审阅）。data、work、原始资料、交付目录、STK工程和缓存不进入普通提交。

```powershell
git status --short
git diff --check
git diff -- README.md
git add .gitignore README.md requirements-smoke.txt configs src tests tools docs
git commit -m "fix: preserve acceptance reasons and validate frozen-grid STK aggregation"
```

以上暂存与提交命令仅供用户手动执行；代理不执行任何暂存、提交、推送或PR操作。
