# macOS 精简实验工具

按项关闭不用的 macOS 后台服务，查看真实运行状态，并按执行前快照恢复。提供中文脚本入口、终端选择界面、预设和内存/进程对比工具。

以 [zhaoyangtop/macos-debloat](https://github.com/zhaoyangtop/macos-debloat) 的中文使用方式和服务分类为基础，整合 [OleksandrKrupko/mac-os-debloat](https://github.com/OleksandrKrupko/mac-os-debloat) 的 Python 服务管理、TUI、状态识别及开机保持功能。双方均为 MIT 许可证，版权声明见 [LICENSE](LICENSE)。

## 快速开始

macOS 26 / 27，Python 3.9+。本机实验使用 Apple Silicon；其他系统版本需先查看 `--audit`。

```bash
git clone https://github.com/nextwa/macos-debloat-zh.git
cd macos-debloat-zh
./macos_disable_bloat_services.sh --dry-run
./macos_disable_bloat_services.sh
```

无参数进入终端选择界面：方向键移动，空格勾选，回车应用，`r` 刷新，`q` 退出。服务英文标识与原始技术说明保留，便于查询系统文档。

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
| `--disable-all` | 整个目录，包含 AirDrop 和应用搜索 |
| `--enable-all` | 启用整个目录 |

自定义预设放在状态目录的 `presets/名称.txt`，每行一个服务标识；`labels.txt` 可以补充目录。`extras/` 包含上游的动画开关脚本，可独立运行。

## 内存与进程实验

```bash
python3 tools/experiment.py capture --output reports/before.json
./macos_disable_bloat_services.sh --preset extreme-keep-airdrop-search --attempt-protected
python3 tools/experiment.py capture --output reports/after.json
python3 tools/experiment.py compare reports/before.json reports/after.json --output reports/comparison.md

# 自动记录基线、执行、等待 30 秒和 90 秒后记录
sudo python3 tools/experiment.py run --output reports/run --attempt-protected
```

报告记录物理内存、压缩内存、交换空间、CPU、全机进程、目标服务及其 RSS。原始数据保留在本地，不包含进程启动参数。`reports/` 默认不提交。

## SIP 与开机保持

SIP 开启时，macOS 可能拒绝停止某些服务，也可能忽略禁用标记并重新启动它们。程序分别报告配置和运行状态；退出码 `2` 表示仍有目标未完成。`--attempt-protected` 不修改 SIP。

在 SIP 开启且存在禁用项目时，程序安装 `io.github.nextwa.macos-debloat-zh` 开机守护程序，重新应用已保存的服务选择。它只处理保存的禁用项目，不会启用清单外的服务；每次开机对反复启动的进程只补充停止一次。`--enable-all` 或恢复到没有该程序的快照会移除它。

## 验证

```bash
python3 -m unittest discover -s tests -v
```

测试使用模拟 launchctl/mdutil，不操作真实系统服务。覆盖实际域识别、部分失败、只读预览、AirDrop/应用搜索保留、跨域恢复及已禁用但仍运行的进程。

## 已知限制

- 关闭服务不会卸载应用、删除 AI 模型或清理用户数据。
- 实际可关闭数量随 macOS 构建和 SIP 状态变化，服务目录数量不是成功数量。
- Spotlight 索引保留，文件搜索和索引开销也随之保留。
- 开机保持不是绕过 SIP；系统保护的服务仍可能运行。
- RSS 求和包含共享页面，不能视为独占物理内存；短时对比还会受到前台应用和缓存变化影响。
- 完整 AirDrop 验收需要另一台设备实际收发；单看进程和窗口不能证明传输成功。
- 动画附加脚本的恢复会写回默认值；它不属于服务状态快照。
