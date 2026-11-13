# Mac兼容性检查与验证（2026-11-13）

结论：可以提供 macOS 15+ Intel / Apple Silicon 兼容候选。已在独立分支修复Windows专用环节，并通过两类云端Mac原生测试；尚未完成个人Mac浏览器收藏和手机5G的用户实机验收，不发布为全链路稳定版。

候选分支：`compat/macos-candidate-20261113`。原 `main` 与 `v0.3.0-beta` Windows发布包、个人运行工作台、现有手机地址及生产数据均未改变。

## 发现与修复

| 原问题 | 候选处理 |
|---|---|
| 密钥只能用Windows DPAPI保存 | Mac固定使用系统Keychain，SQLite只存标记；按数据目录隔离；失败报错，不降级明文 |
| 扫码仅能下载Windows EXE，进程参数也专用 | 分Intel/ARM下载官方固定归档，验证归档与可执行文件SHA256、安全读取文件、设置执行位；Mac进程不传Windows flags |
| 只有.cmd入口 | 新增安装、启动、重置密码.command；补Homebrew常用PATH，启动用项目.venv且服务独立于终端 |
| Tk缺失时无法重置密码 | 交互终端隐藏输入后更新密码并撤销旧会话；不接收参数/管道密码 |
| Mac设置可能误选CUDA | 后端固定CPU转写；设置读取服务端平台，Mac显示CPU并解释未接苹果GPU |

没有修改推荐筛选、预算、采集频率或浏览器登录策略。浏览器端仍用现有MV3扩展，Safari不承担收藏采集。

## 已完成验证

原生运行提交：`88eb786d7027510b2ed6275d0b42d8a9dd9a8a41`。

| 环境 | 安装/构建 | 后端 | 前端 | 原生检查 |
|---|---|---|---|---|
| Windows本机 Python3.10 | 通过 | 246通过、12项退役功能跳过 | 5通过 | 模拟平台适配，不冒充Mac |
| GitHub Mac ARM64，macOS15.8.2，Python3.12.10 | 通过，含可选转写依赖 | 246通过、12跳过 | 5通过 | Keychain、官方程序执行、后台启动/首次密码/登录/网页均通过 |
| GitHub Mac Intel x86_64，macOS15.7.9，Python3.12.10 | 同上 | 246通过、12跳过 | 5通过 | 同上 |
| GitHub Windows回归 | 通过 | 通过 | 通过 | 原有Windows流程保留 |

- [Mac原生完整运行记录](https://github.com/jimaofeishangtian-ai/creator-workbench/actions/runs/43438286351)
- [Windows云端回归](https://github.com/jimaofeishangtian-ai/creator-workbench/actions/runs/43438285966)
- Python3.10目标依赖解析：Intel与ARM各58项，全部有目标兼容wheel；没有仅替换系统名称而跳过安装检查。
- 转写原生检查只导入faster-whisper/CTranslate2并验证CPU类型，没有下载Whisper权重或宣称测得转写速度。
- 官方cloudflared 2026.10.7两归档与内部程序实际校验，Mach-O最低系统字段均为15.0.0，因此完整候选设macOS15+。执行了`--version`，没有在CI打开公网隧道。
- 归档模拟验证：错误哈希、篡改程序、路径穿越、符号链接均拒绝，成功缓存不会重复下载；钥匙串拒绝不会保存明文或泄漏底层详情。
- 原生凭据只在一次性CI机器写入合成值并删除。没有抖音登录、真实收藏或付费API调用。生产健康接口仍返回200。

原生测试后只补服务端平台提示与Mac CPU设置展示：相关33项测试通过，TypeScript/Vite重新构建通过；复用未受影响的原生安装/钥匙串/组件证据，不重复整套付费或平台采集。

## 尚待真实用户确认

1. Mac首次下载保护、钥匙串授权提示与本人已有Chrome/Edge扩展加载。
2. 本人抖音收藏实际读取及增量同步，不读取Cookie、不重登。
3. 电脑联网开启扫码后，手机关闭Wi-Fi/VPN，用5G真实登录；临时地址重建会变。
4. FFmpeg、实际音频转写准确率、模型下载和CPU速度。Apple GPU / MLX 未接入。

候选源码和[安装边界](../references/macos.md)可用于后续Mac试用；现有PDF与飞书领取页仍保留Windows beta口径，不将计划或CI能力写成个人实测。
