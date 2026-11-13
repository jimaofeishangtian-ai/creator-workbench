# Mac 兼容候选（非已发布稳定版）

本分支补齐 macOS 15+ 的 Intel / Apple Silicon 适配；旧 Windows beta 下载不因本分支而自动支持 Mac。模拟、依赖解析和原生测试分别记录。

## 环境与安装

- 完整目标 macOS 15+，Intel 或 Apple Silicon 原生 Python 3.10—3.12，建议 3.12；Node.js 22.12+。M 系列不要混用 Rosetta 的 Intel Python 与 ARM 依赖。
- Agent 需要 Mac 本机文件与命令能力，首次安装联网。包放在本人可写的固定目录；不要放只读磁盘或搬动已安装的 .venv。
- 包根目录先运行 `python3 scripts/workbench.py doctor`，再 `python3 assets/workbench/scripts/install.py`，最后 `python3 assets/workbench/scripts/start.py`。
- 另提供 assets/workbench 内「安装工作台.command」「启动工作台.command」「重置密码.command」。ZIP 解压若丢失执行位，可由 Agent 对这三文件 `chmod u+x`。遇下载保护按 macOS 对具体文件的正常打开流程处理，不关闭 Gatekeeper 或全局安全设置。
- 首次由用户设置至少8位密码；启动器关闭后服务独立运行。数据仍只在本包 runtime/，不读取其他实例。8765已有其他工作台就停止，不接管。

## 能力与边界

| 能力 | Mac 路线 |
|---|---|
| 推荐、筛选、对标、收藏整理、拆解、任务/费用/备份 | 共用 React / FastAPI / SQLite，账户与密钥由本人配置 |
| API 密钥 | 固定使用原生 macOS Keychain；可能询问用户授权。锁定或拒绝时明确报错，不降级明文；每个数据目录独立，迁移后重配 |
| 抖音收藏 | Mac Chrome / Edge 的现有登录 + MV3扩展；Safari可看工作台，不能加载此 Chromium 扩展。真实收藏同步待Mac用户验收 |
| 手机5G扫码 | 固定 Cloudflare 2026.10.7，按架构下载并校验归档和程序 SHA256，程序仅用户可执行；Mach-O最低系统为15，旧系统明确提示 |
| 本地转写 | 可选 `--with-asr` + FFmpeg。优先字幕，否则CPU/int8 Whisper；没有接苹果Metal/MPS，不能宣传苹果GPU加速。首次模型下载与实际速度待实测 |
| 密码重置 | 首选Tk窗口；没有Tk时本人终端隐藏输入两遍，不接受密码参数或管道输入 |

不读取 Cookie、不迁移 profile、不重新登录验证。收藏依赖电脑和浏览器在线；手机地址临时、重建会变、关机休眠断开。公开包不含运行数据和账号。

## 分层验证

1. Windows上模拟架构选择、钥匙串成功/失败、哈希/路径/符号链接拒绝、缓存、进程参数和密码重置，不执行Mac程序。
2. 两架构依赖预解析，确认声明版本有目标轮子；不等同动态库已加载。
3. 候选CI用 macos-15（ARM）与 macos-15-intel：实际安装/构建、全量无付费测试、原生钥匙串合成密钥读写清除、cloudflared --version、CPU转写库导入、后台启动和本机登录。
4. 待用户实机：首次钥匙串/下载授权、收藏扩展、手机外网扫码、真实转写质量和性能。CI不登录抖音，不调用TikHub/DeepSeek。

## 核对来源（2026-11-13）

- [Cloudflare下载](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/downloads/)、[固定版本](https://github.com/cloudflare/cloudflared/releases/tag/2026.10.7)：已核验两归档/程序哈希及Mach-O最低系统。
- [CTranslate2系统与GPU支持](https://opennmt.net/CTranslate2/installation.html)：Mac Intel/ARM可用，预编译CUDA GPU为Linux/Windows路线。
- [原生钥匙串后端](https://github.com/jaraco/keyring/tree/v25.7.0/keyring/backends/macOS)。
- [GitHub标准Mac runner](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)：云端系统测试不代替用户浏览器与手机验收。
