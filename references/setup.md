# 安装与宿主兼容

首发为Windows本地分享测试版。推荐Python3.10—3.12、满足Vite7要求的Node.js（20.19+或22.12+），现有浏览器；首次安装需联网。FFmpeg用于HEIC封面转换与本地语音转写；普通JPEG/WebP/PNG封面和关键词推荐不需要它，转写模型只在语音转写时安装。

## 宿主

- WorkBuddy官方文档说明可上传本地技能包；在“添加技能/上传Skill”选择ZIP。是否具备可控的本机浏览器、文件与命令工具仍由运行时检查。
- 豆包工作：有本地文件/命令能力时解压到授权目录，发送“读取根目录SKILL.md并从环境检查开始”。普通聊天附件或云端执行不等于连接本人本机登录。未在豆包工作实际机器验收，不承诺平台所有版本原生安装格式一致。
- Codex/Claude Code等：将根目录作为技能目录或要求读取SKILL.md。所有工具按宿主自己的权限和接口使用，没有强依赖Codex的MCP名称。

官方资料（2026-11-07核对）：

- WorkBuddy：[Skills导入](https://www.workbuddy.ai/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Skills)
- 豆包：[Skill接入指南](https://open.doubao.com/document/doubao-developer-guide/skill-integration-guide)说明Skill与连接器职责不同；开放平台接入不证明某个客户端已有本地浏览器权限。

## 操作

在包根目录运行：

```
python scripts/workbench.py doctor
python assets/workbench/scripts/install.py
python assets/workbench/scripts/start.py
```

也可以双击assets/workbench/内“安装工作台.cmd”“启动工作台.cmd”。安装器创建项目自己的.venv，安装声明依赖并构建页面，不会安装浏览器扩展或代登录。FFmpeg提示不影响收藏。

首次页面在http://127.0.0.1:8765/，设置码只由本机启动器读取，用户自己设置密码。不要删除auth、复制会话或把初始码发送远端。若本机已有另一套8765工作台，启动器会拒绝接管；先正常退出旧服务，或由有能力的Agent做独立端口与扩展配置适配，不能只改服务端口忘记扩展。

数据在assets/workbench/runtime/。该目录以及.venv、node_modules、缓存、API密钥、浏览器配置、日志、用户访谈和导入关注列表**不要再次打包分享**。新用户须用自己的账号与密钥。

手机可在「手机与设备」按需开启扫码连接，或用本包scripts/phone-access.py；首次会下载校验官方Cloudflare组件。临时公网HTTPS网址配合工作台独立登录，用户自己设置至少8位密码。电脑关机/休眠断开，重建连接会换网址，5G验收由用户实际完成；公网禁止密钥/配对/设置修改。不开启时不暴露公网。不默认启用开机启动。

2026-11-08封面兼容：工作台通过本机受认证接口读取并缓存封面，HEIC封面使用FFmpeg转换为JPEG。无需重新登录抖音，也不额外调用付费数据接口；缺少FFmpeg或封面已失效时显示占位提示，仍可打开原片。需要HEIC封面时在本机安装FFmpeg；普通JPEG/WebP/PNG无需转换。
