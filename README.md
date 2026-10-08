# macOS 内存与服务工具

查看内存压力及应用占用，请求应用释放可丢弃缓存，并记录前后变化。也可以按项管理 macOS 后台服务，区分禁用配置和实际运行状态，按执行前快照恢复。提供中文终端界面、搜索与筛选、执行预览及两类独立实验。

以 [zhaoyangtop/macos-debloat](https://github.com/zhaoyangtop/macos-debloat) 的中文使用方式和服务分类为基础，整合 [OleksandrKrupko/mac-os-debloat](https://github.com/OleksandrKrupko/mac-os-debloat) 的 Python 服务管理、TUI、状态识别及开机保持功能。双方均为 MIT 许可证，版权声明见 [LICENSE](LICENSE)。

## 快速开始

macOS 26 / 27，Python 3.9+。本机实验使用 Apple Silicon；其他系统版本需先查看 `--audit`。

```bash
git clone https://github.com/nextwa/macos-debloat-zh.git
cd macos-debloat-zh
./macos_disable_bloat_services.sh --memory-ui
# 打开服务管理界面
./macos_disable_bloat_services.sh
```

也可以在 Finder 中双击 `打开内存面板.command` 或 `启动精简工具.command`。

## 内存面板与应用缓存回收

内存面板显示压力级别、压缩器占用、Wired、文件缓存、交换空间，以及按应用汇总的内存足迹和 RSS。应用包内的 Helper 和可追溯父进程的子进程归入同组，无法归属的系统进程单列。

| 按键 | 操作 |
|---|---|
| `↑` / `↓`、`j` / `k`、`PgUp` / `PgDn` | 移动应用列表 |
| `/`、`r` | 搜索应用、刷新采样 |
| 回车 | 查看所选应用的 PID、读数覆盖范围与应用路径 |
| `o` | 预览缓存回收，按 `y` 确认后执行 |
| `v` | 最近一次缓存回收结果 |
| `Esc` / `q` | 返回或退出 |

```bash
./macos_disable_bloat_services.sh --memory
./macos_disable_bloat_services.sh --memory --json
./macos_disable_bloat_services.sh --reclaim-memory --dry-run
./macos_disable_bloat_services.sh --reclaim-memory
./macos_disable_bloat_services.sh --memory-report --json
```

回收操作使用 macOS 自带的 `memory_pressure -S -l warn -s 1`，模拟 1 秒的 warn 级别内存压力，让响应通知的应用释放可丢弃缓存。执行需要管理员认证。内存压力紧张或状态无法识别时会跳过；压力正常时允许手动实验，并说明收益可能很小。

结果记录在 `~/Library/Application Support/macos-debloat-zh/last-memory.json`，包含前后分类数据、交换读写增量，以及按 PID 和启动时间匹配的同批进程足迹变化。内存操作独立于服务选择和开机保持配置。

内存模块参考 [WonderBox](https://github.com/jasonwong1991/WonderBox) 的应用聚合与分类对比方式，以及 [mac-ram-cleaner](https://github.com/chumafox/mac-ram-cleaner) 的短时模拟压力方式；本项目使用 Python 标准库和 macOS 系统接口实现。指标含义见 [Apple 活动监视器说明](https://support.apple.com/guide/activity-monitor/view-memory-usage-actmntr1004/mac)。

## 交互界面

界面顶部显示本机服务数、禁用数、运行数、禁用但仍运行数，以及 SIP 和开机保持状态。服务按中文分类展示，英文服务标识和原始技术说明保留，便于查找系统文档。建议终端至少为 80 列、24 行；低于 62 列、18 行会提示扩大窗口。

1. 选择预设后按回车，或用空格修改单项选择。预设只准备计划。
2. 按 `a` 查看逐项执行预览，再按 `y` 确认；取消会保留选择。
3. 执行前自动保存快照，完成后立即显示结果。按 `v` 可再次查看。

| 按键 | 操作 |
|---|---|
| `↑` / `↓`、`j` / `k` | 移动 |
| `PgUp` / `PgDn`、`[` / `]` | 翻页、切换分类 |
| 空格 | 修改单项选择 |
| `/` | 按服务名称、中文分类或说明搜索 |
| `f` | 切换全部、禁用仍运行、保留功能、待更改 |
| `a` | 预览并应用当前选择 |
| `m` | 打开内存面板，保留未执行的服务选择 |
| `r` | 重新读取系统状态，清除未执行的选择 |
| `v` / `?` | 最近结果、操作与 SIP 说明 |
| `Esc` / `q` | 清空搜索筛选、退出 |

`[✓]` 表示请求允许启动，`[ ]` 表示请求禁用，`[-]` 保留各域现有的混合配置，`*` 表示待更改。右侧状态独立显示实际结果：

| 状态 | 含义 |
|---|---|
| 已禁用·未运行 | 所有所属域都有禁用标记，当前没有运行进程 |
| 禁用但仍运行 | 禁用标记已写入，进程仍然存在 |
| 部分域仍禁用 | 同一服务在不同域中的配置不一致 |
| 允许·运行中 | 允许启动，当前有进程 |
| 允许·按需启动 | 允许启动，当前没有进程 |

## 全量精简，保留 AirDrop 和应用搜索

```bash
# 预览
./macos_disable_bloat_services.sh --preset extreme-keep-airdrop-search --dry-run

# 应用：保留 SIP 对受保护项目的限制
./macos_disable_bloat_services.sh --preset extreme-keep-airdrop-search

# 对标记为需要关闭 SIP 的服务也尝试一次，记录系统拒绝和重新启动的进程
./macos_disable_bloat_services.sh --preset extreme-keep-airdrop-search --attempt-protected

# 查看实际结果
./macos_disable_bloat_services.sh --verify --json

# 查看最近执行的逐项失败原因及恢复快照
./macos_disable_bloat_services.sh --report
```

此预设选择目录中所有本机存在的服务，再保留以下依赖。已经被禁用的保留项目会重新启用。

| 保留功能 | 相关依赖 |
|---|---|
| AirDrop | sharingd、rapportd、nearbyd、Wi-Fi 点对点、蓝牙、联系人和账号身份识别 |
| 应用搜索 | campo、Spotlight、CoreSpotlight、metadata 索引及 LaunchServices |
| 系统运行 | 桌面、音频、网络、权限、输入、电源与系统看护服务 |

AirDrop 联系人模式需要身份与联系人信息，不能把这些共享依赖同时关闭。[Apple AirDrop 安全说明](https://support.apple.com/guide/security/airdrop-security-sec2261183f4/web)

“全量”指本工具服务目录内的可选后台功能。该预设会影响 Siri、Apple Intelligence、iCloud 同步、信息/FaceTime、媒体分析、App Store、系统更新安装、打印等功能。共享依赖保留后，部分关联功能也可能继续运行。

## 恢复

```bash
# 恢复最近一次执行前的服务配置及 Spotlight 开关
./macos_disable_bloat_services.sh --restore

# 恢复某个指定实验快照
./macos_disable_bloat_services.sh --restore '/路径/快照.json'

# 将目录中的服务全部启用，并移除本工具的开机保持程序
./macos_disable_bloat_services.sh --enable-all
```

快照保存在当前用户的 `~/Library/Application Support/macos-debloat-zh/snapshots/`。每次应用保存用户域和系统域各自的原始禁用状态；恢复不会覆盖原始快照。恢复的是启动配置，不会重建原进程的 PID 或内存内容。

## 其他命令

| 参数 | 作用 |
|---|---|
| `--preset telemetry` | 遥测预设 |
| `--preset balanced` | 均衡预设，包含 AirDrop，保留 AirDrop 时请用专用预设 |
| `--list` | 本机实际存在的服务目录 |
| `--audit` | 不存在或不适合当前版本的服务 |
| `--status` / `--verify` | 禁用标记、运行进程、SIP 和开机保持状态 |
| `--status --json` | 每项服务的状态、PID、所属域、各域禁用配置和保留用途 |
| `--report [--json]` | 最近执行结果、SIP 拒绝卸载的具体服务及其他失败原因 |
| `--disable-all` | 整个目录，包含 AirDrop 和应用搜索 |
| `--enable-all` | 启用整个目录 |

自定义预设放在状态目录的 `presets/名称.txt`，每行一个服务标识；`labels.txt` 可以补充目录。`extras/` 包含上游的动画开关脚本，可独立运行。

## 内存与进程实验

应用缓存回收实验：

```bash
python3 tools/memory_experiment.py --output reports/memory-run
```

程序先完成管理员认证，采集 10 秒无操作对照，再执行一次缓存回收，记录约 3 秒、30 秒和 120 秒后的变化，生成 JSON 原始数据和 `comparison.md`。正常使用当前应用即可；比较时应保持相同工作负载。

服务精简实验：

```bash
python3 tools/experiment.py capture --output reports/before.json
./macos_disable_bloat_services.sh --preset extreme-keep-airdrop-search --attempt-protected
python3 tools/experiment.py capture --output reports/after.json
python3 tools/experiment.py compare reports/before.json reports/after.json --output reports/comparison.md

# 自动记录基线、执行、等待 30 秒和 90 秒后记录
sudo python3 tools/experiment.py run --output reports/run --attempt-protected
```

报告记录物理内存、压缩内存、交换空间、CPU、全机进程、目标服务及其 RSS。原始数据保留在本地，不包含进程启动参数。`reports/` 默认不提交。

### macOS 27 实机记录

2026 年 10 月 8 日，在 Apple M5、16 GiB、macOS 27.0.1、SIP 开启的机器上执行一次应用缓存回收。回收完成后约 120 秒的观察结果：

| 指标 | 回收前 | 约 120 秒后 | 变化 |
|---|---:|---:|---:|
| 压缩器实际占用 | 2,936.3 MiB | 2,705.0 MiB | -231.3 MiB |
| Wired 内存 | 2,068.5 MiB | 2,015.4 MiB | -53.1 MiB |
| 文件缓存页 | 3,729.3 MiB | 3,717.6 MiB | -11.7 MiB |
| 空闲与推测页 | 916.8 MiB | 1,312.3 MiB | +395.5 MiB |
| 交换空间占用 | 0 MiB | 0 MiB | 0 MiB |
| 进程数 | 793 | 684 | -109 |

压缩器占用在约 3 秒、30 秒和 120 秒采样中分别下降 217.4、221.1 和 231.3 MiB。期间交换读入、写出均为零，内存压力保持正常；执行前的 47 个应用组在最后一次采样中均仍存在。原有服务守护程序在实验窗口内没有执行检查。

## SIP 与开机保持

SIP 开启时，macOS 可能拒绝停止某些服务，也可能忽略禁用标记并重新启动它们。程序分别报告配置和运行状态；退出码 `2` 表示仍有目标未完成。`--attempt-protected` 不修改 SIP。

在 SIP 开启且存在禁用项目时，程序安装并启动 `io.github.nextwa.macos-debloat-zh` 开机守护程序，保存禁用项目和明确保留启用的 AirDrop、搜索、基础依赖。开机、登录及后续检查会重新应用这两份清单，不改动清单外的服务；每次开机对反复启动的进程只补充停止一次。

每次应用会同步程序版本与选择，重新加载守护程序，使当前会话使用新配置。`--enable-all` 或恢复到没有该程序的快照会移除它；恢复已有守护程序的快照时，使用快照中的原始选择。

SIP 拒绝卸载会单独列出服务和所属域。即使服务当时没有进程，卸载失败也会返回部分完成，不会把“暂时未运行”算作卸载成功。首次部署或同步开机保持失败、恢复注册失败，也会显示具体结果。

## 验证

```bash
python3 -m unittest discover -s tests -v
```

测试使用模拟 launchctl/mdutil 和内存回收请求，不操作真实系统服务。覆盖实际域识别、SIP 拒绝、逐项状态、跨域恢复、保留项开机重新启用、守护程序更新、应用进程聚合、Apple Silicon 页大小、不可读足迹、交换写出及进程重启比较。两类交互预览取消均不请求管理员权限。中文布局覆盖常见终端尺寸。

## 已知限制

- 应用是否释放缓存由其自身实现决定；通知成功不等于释放成功，也不能保证持续性能提升。
- 足迹来自 `proc_pid_rusage` 的系统计费口径，不能与 RSS 混加。部分系统进程需要更高权限才能读取；`≥` 标记部分读数，不可读值不会当作零占用。
- 共享 XPC 服务未必能归属到某个应用；进程分组不等于完整的系统资源归因。
- 缓存可在继续使用后增长，交换活动和前台任务会影响结果。文件缓存减少或空闲页增加不能单独证明改善。
- 上述实机实验中，同批 446 个进程的足迹在 120 秒时下降 66.5 MiB，但回收前的 10 秒对照期也已自然下降约 65.2 MiB，Wired 下降约 419 MiB。差值不能全部归因于回收；单机两分钟观察也不能证明长期或任务速度收益。
- 关闭服务不会卸载应用、删除 AI 模型或清理用户数据。
- 实际可关闭数量随 macOS 构建和 SIP 状态变化，服务目录数量不是成功数量。
- Spotlight 索引保留，文件搜索和索引开销也随之保留。
- 开机保持不是绕过 SIP；系统保护的服务仍可能运行。
- RSS 求和包含共享页面，不能视为独占物理内存；短时对比还会受到前台应用和缓存变化影响。
- 完整 AirDrop 验收需要另一台设备实际收发；单看进程和窗口不能证明传输成功。
- 动画附加脚本的恢复会写回默认值；它不属于服务状态快照。
