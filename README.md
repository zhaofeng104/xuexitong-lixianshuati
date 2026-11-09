# 学习通刷课助手 · 图形界面版

> 超星学习通（Chaoxing）自动化任务点助手 —— Windows 图形界面版，绿色免安装，双击即用。
>
> **本项目是开源衍生作品，非原创首发**。内核基于 [Samueli924/chaoxing](https://github.com/Samueli924/chaoxing)（GPL-3.0），
> 验证码模块版权归 **skreon**，缓存与字体解码版权归 **SocialSisterYi**。
> **完整的第三方版权与许可声明见 [NOTICE.md](NOTICE.md)。**

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows-lightgrey.svg)]()
[![Python](https://img.shields.io/badge/python-3.13-3776ab.svg)]()

---

## ⚠️ 许可与使用限制（先读这段）

本项目**继承上游 GPL-3.0 的全部约束**，包括上游作者明确列出的三条：

| 上游原话 | 本项目如何执行 |
|---|---|
| 不允许修改和衍生的代码作为**闭源的商业软件**发布和销售 | 源码 100% 公开，无任何闭源模块 |
| **禁止使用本代码盈利** | 完全免费，无广告、无付费墙、无捐赠入口、不卖授权 |
| 本代码**仅用于学习讨论** | 仅作技术学习用途，不提供任何商业服务 |

**如果你要拿这个项目赚钱、放进付费课程、或做成闭源产品 —— 不允许。**
这是上游作者定的规则，不是我的，请尊重。

---

## 这是什么

把命令行版的学习通自动化内核，包成一个**双击就能用**的 Windows 桌面程序：

- **零安装**：整个文件夹拷走就能跑，不写注册表、不改系统环境、自带独立 Python 运行时
- **图形界面**：账号、课程勾选、参数、运行日志全在一个窗口里，不用敲命令
- **Cookie 登录**：账号开了短信/滑块验证时，粘一段浏览器 Cookie 一样能跑
- **断点续跑**：中途关掉重开，进度接着走
- **题库对接**：章节测验可接第三方题库（可选，不填也能跑，只是慢）
- **任务增强**：文档自动翻阅、直播自动签到、人脸提醒

## 快速开始

### 方式一：下载成品（推荐普通用户）

1. 到 [Releases](../../releases) 下载 `学习通刷课助手-vX.Y.Z-win64.zip`
2. 解压到任意目录（**路径别带空格和特殊符号**）
3. 双击 `学习通刷课助手.exe`

> Windows 可能提示"已保护你的电脑"，点「更多信息」→「仍要运行」。
> 程序**未做代码签名**，这是预期行为；源码全部在本仓库，可自行审计或自行构建。

### 方式二：从源码运行（开发者）

```bash
git clone https://github.com/zhaofeng104/xuexitong-lixianshuati.git
cd xuexitong-lixianshuati
pip install -r core/requirements.txt
python -m app.ui
```

需要 Python 3.10+。界面只用标准库 Tkinter，无额外 UI 依赖。

## 目录结构

```
.
├── app/                      图形界面与稳定层（本项目原创）
│   ├── ui.py                 主窗口（Tkinter）
│   ├── runner.py             后台线程调度与生命周期
│   ├── launch_core.py        启动内核子进程
│   ├── patches.py            内核缺陷运行时补丁（9 项）
│   ├── enhance.py            功能增强（文档翻阅 / 直播 / 人脸提醒）
│   ├── check_env.py          依赖自检
│   ├── make_dist.py          一键构建交付包
│   ├── build.py              从上游源码重建 core/
│   └── core_patches/         源码级补丁备份（暂停功能）
├── core/                     程序内核（上游 GPL-3.0 衍生）
│   ├── main.py               内核入口
│   ├── api/                  请求、答题、直播、字体解码等模块
│   │   ├── captcha.py        ← 版权归 skreon（1340554713@qq.com）
│   │   ├── cxsecret_font.py  ← 版权归 SocialSisterYi
│   │   └── answer.py         ← CacheDAO 版权归 SocialSisterYi
│   ├── resource/             字体映射表
│   ├── LICENSE               GPL-3.0 原文
│   └── README.md             上游说明
├── LICENSE                   本项目整体许可证（GPL-3.0 + 版权头）
├── NOTICE.md                 **完整第三方版权声明（必读）**
├── CHANGELOG.md              版本变更与九项修复明细
├── 总说明.md                  完整使用手册（参数、排障、FAQ）
├── 启动.bat / 关闭程序.bat     命令行入口与急救脚本
└── 环境自检.bat               依赖体检
```

## 这一版修了什么

内核层做了 9 项缺陷修补（`app/patches.py`，每项可单独开关），主要几项：

1. **暂停功能失效** —— 原实现暂停期间时间照走，进度虚高；改为在发包循环内插入检查点
2. **Cookie 并发覆盖** —— 多线程 relogin 时 `session.cookies` 被整体覆盖；加可重入锁串行化
3. **静默挂死** —— 章节处理抛异常会让工作线程死掉、`join()` 永久阻塞；加崩溃兜底
4. **视频结尾不被认可** —— `isdrag=3`（拖动）改为 `isdrag=4`（自然播完），这是最高频的失败原因
5. **验证码死磕** —— 每轮 3 次改 1 次，不再白等 60 秒

完整对照表（含每项不修的后果）见 **[CHANGELOG.md](CHANGELOG.md)**。

## 数据流向说明（重要）

程序**不上传你的本地文件，不采集设备指纹，不写注册表，不加自启动**。但以下数据会离开本机：

| 目的地 | 发送内容 | 何时发 |
|---|---|---|
| 超星各子域 | 你的账号会话、课程/章节 ID、学习行为 | 全程 |
| `detect.chaoxing.com` | 模拟真人的监控心跳（含学校 `fid`） | 每个任务点 |
| 题库服务（需自行填 token） | **题目原文** | 章节测验 |
| `api.deepseek.com` / `api.siliconflow.cn` | 题目原文 + 你的 Key | 启用 AI 答题时 |

题库默认**不填 token 也能跑**，此时题目不会发给第三方，只是测验环节命中率低。

本地会在 `core/chaoxing.log` 记录运行日志（TRACE 级别，10 MB 滚动），
**用 Cookie 登录时该日志可能包含会话凭据**，外发前请先清理。

## 已知限制

- 仅 Windows（启动器为 C# 编译的 exe，界面依赖 Win32 控制台编码行为）
- 题库为第三方服务，Token 需自行申请；不填则只能靠默认题库
- 学习通接口会变，风控会升级，**本项目不保证长期可用**
- 未做代码签名，杀软可能误报（源码在此，可自行构建）

## 致谢

| 贡献者 | 贡献 |
|---|---|
| [Samueli924](https://github.com/Samueli924/chaoxing) | 命令行内核（GPL-3.0） |
| **skreon** (1340554713@qq.com) | 验证码识别模块 `core/api/captcha.py` |
| **SocialSisterYi** | `CacheDAO` 缓存层、`cxsecret_font.py` 字体解码 |
| [sml2h3/ddddocr](https://github.com/sml2h3/ddddocr) | 开源验证码 OCR |
| [DokiDoki1103/tikuAdapter](https://github.com/DokiDoki1103/tikuAdapter) | 题库适配参考 |

## 免责

本项目与超星 / 学习通官方**无任何关联**。使用自动化工具可能违反平台用户协议
及所在学校的学术诚信规定，**后果由使用者自行承担**。

## 联系

- 本项目维护：**zhaofeng104** · 3457222856@qq.com
- `captcha.py` 模块问题请联系原作者 **skreon** · 1340554713@qq.com
- 内核问题请走上游仓库 [Samueli924/chaoxing](https://github.com/Samueli924/chaoxing)
