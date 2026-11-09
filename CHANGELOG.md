# Changelog

本项目从 v1.0.0 起记录变更。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

---

## [1.0.0] - 2026-11-09

首个公开版本。相比上游命令行内核，这一版做了三件事：**加界面、补内核缺陷、做成可交付的绿色包**。

### 新增

- **图形界面**（`app/ui.py`）：账号 / 课程 / 参数 / 日志四区一体，纯 Tkinter，无第三方 UI 依赖
- **Cookie 登录**：粘贴浏览器 Cookie 即可绕过短信与滑块验证
- **暂停 / 继续**：`app/core_patches/api/pause.py` 新增暂停模块，暂停期间计时冻结
- **任务增强**（`app/enhance.py`，默认全开，可单独关）：
  - `link_task` —— 链式任务自动跟随
  - `document_turn` —— 文档任务自动翻页
  - `face_notify` —— 人脸识别任务主动提醒
- **环境自检**（`app/check_env.py`）：逐项检查依赖，缺 `ddddocr` 只警告不中断
- **一键打包**（`app/make_dist.py`）：构建 + 自检 + 压 zip，自动剔除隐私文件并复查
- **诊断包导出**：把日志与环境信息打包给作者排查，`password` / `token` / `secret` / `Cookie` 等键值自动打码

### 修复 —— 内核层九项补丁（`app/patches.py`，每项可单独开关）

| # | 补丁 | 修的是什么 | 不修的后果 |
|---|---|---|---|
| 1 | `patch_session_manager` | `SessionManager.__new__` 返回单例时 `__init__` 仍会执行，导致每次 `get_instance()` 都新建一个 `requests.Session` | 每次重读 `cookies.txt`（磁盘 IO）、旧连接不关（句柄累积）、并发下 cookie 互相覆盖；长跑几小时后句柄与内存缓慢上涨 |
| 2 | `patch_isdrag` | 视频到达结尾时强制 `isdrag=4`（自然播完）而非默认的 `3`（拖动跳转） | 平台不认「播放完毕」，日志表现为「已到末尾但平台连续 N 次未确认完成」——**最高频的失败原因** |
| 3 | `patch_network_resilience` | 包住 `process_chapter`，网络错误按指数退避重试同一章，超过 `NETWORK_MAX_WAIT` 才放弃 | 网络抖动让 `worker_thread` 直接死掉，`task_queue.join()` 永久阻塞，**整个程序静默挂死**（界面还在、进度不动） |
| 4 | `patch_session_concurrency` | 连接池放大到 `pool_maxsize=32`；给 `Session.request` 加可重入锁，串行化「读 cookie → 发请求」 | 池满时 `Connection pool is full, discarding connection` 静默丢连接；relogin 覆盖 cookie 时其他线程读到半更新状态，带错凭证 |
| 5 | `patch_crash_guard` | 章节处理中的任何未预期异常（`KeyError` / 解析失败 / HTML 改版）都被兜住并 `task_done()` | 线程静默死亡 + `join()` 永久阻塞 + 整个程序挂住，且**日志毫无线索** |
| 6 | `patch_decode_course_folder` | 修 `decode_course_folder` 空指针：`select_one("input.rename-input").attrs["value"]` 在文件夹未命名时返回 `None` | 单个畸形文件夹让整张课程列表读不出来，「识别我的课程」永远失败或一直转圈 |
| 7 | `patch_course_list_diag` | 给 `get_course_list` 逐步打点（哪个请求、哪一步、等了多久） | 卡住时日志只有「开始解码二级课程列表...」然后没了，无法定位 |
| 8 | `patch_captcha_fastfail` | 验证码识别每轮从 3 次改为 1 次，识别不通立刻返回交给外层按 403 处理 | 实测连续试 3×4 = 12 次、白等约 60 秒，最后照样 403 跳过整章；界面看起来像卡死 |
| 9 | `patch_forbidden_deep_retry` | `study_video` 内写死的 `max_forbidden_retry = 2` 不够，包一层临时调大重试预算 | 实测一次 36 分钟运行：11 个视频任务只完成 2 个（18%），403 报错 22 次，而成功的那两个恰恰都是「刷新第 2 次」才过的 |

### 安全与隐私

- 仓库内**不含**任何凭据：`cookies.txt` / `config.ini` / `cache.json` / `*.log` 全部 gitignore
- 诊断包导出时自动打码：`_SECRET_KEYS` 命中 `password` / `passwd` / `token` / `secret`，
  同时对 `Cookie:` / `Set-Cookie:` / `Authorization:` 整行与 URL 里的 `?token=` 做替换
  （`app/ui.py`）
- 打包脚本 `make_dist.py` 在压缩完成后**再复查一遍**，确认没把 `config.ini` / `cookies.txt` /
  `chaoxing.log` / `cache.json` 打进去

### 许可

- `core/` 为 [Samueli924/chaoxing](https://github.com/Samueli924/chaoxing) 的 GPLv3 衍生，原始版权与许可完整保留（`core/LICENSE`）
- 本项目整体以 GPLv3 发布

---

## 上游基线

| 项 | 版本 |
|---|---|
| 上游内核 | [Samueli924/chaoxing](https://github.com/Samueli924/chaoxing) `main` |
| Python 运行时 | 3.13.16（便携版，随 Release 分发） |
| 界面框架 | Tkinter（标准库） |
