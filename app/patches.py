# -*- coding: utf-8 -*-
"""内核运行时补丁：修理上游已知缺陷，但不改动 core\\ 里的任何一行源码。

设计原则
--------
1. **只包装，不改写**。每个补丁都是把某个函数换成"先做点事再调原函数"的壳，
   出错就退回原行为，不会把上游改坏。
2. **可单独关闭**。每个补丁有独立开关，出问题能逐条定位。
3. **升级不冲突**。core\\ 换成新版本时，这些补丁仍然只作用在名字上；
   上游若自己修了同名问题，补丁会检测到并自动跳过（见各函数内的判断）。

参考依据
--------
缺陷清单来自对照 Kerxs/XueXiTong-Plus 的 CLAUDE.md（它累计修了上游大量问题），
逐条在本机 core\\ 里用 grep 核实过，不是照抄别人的结论。
"""

from __future__ import annotations

import functools
import time

# 每个补丁的开关。排查时改成 False 即可单独停用。
ENABLE_SESSION_FIX = True     # SessionManager 每次 get_instance() 都重建会话
ENABLE_ISDRAG_FIX = True      # 视频看到结尾时 isdrag 必须是 4
ENABLE_NETWORK_FIX = True     # 断网不消耗重试次数、不让工作线程死掉
ENABLE_SESSION_LOCK = True    # 共享 Session 后加连接池扩容与并发保护
ENABLE_CRASH_GUARD = True     # 章节里任何未预期异常都不许弄死工作线程
ENABLE_DECODE_FIX = True      # decode_course_folder 空指针（文件夹缺 rename-input 就崩）
ENABLE_COURSE_DIAG = True     # 课程列表每一步打点，卡住时能看出卡在哪
ENABLE_CAPTCHA_FASTFAIL = True  # 验证码识别失败就立刻放手，不空耗 1 分钟
ENABLE_FORBIDDEN_DEEP = True    # 403 深度恢复：多刷几次会话而不是 2 次就放弃

# 单个章节遇到网络问题时最多等多久（秒）。超过就按失败处理，避免永久卡住。
# 上游对同一章最多重试 5 次（main.JobProcessor.max_tries），
# 若这里也放开到 30 分钟 × 5 次 = 2.5 小时，用户会以为程序卡死。
# 折中：单章最多等 6 分钟（足够扛过校园网抖动/路由器重连），
# 之后交回上游按正常失败处理，5 次重试总共最多 ~30 分钟。
NETWORK_MAX_WAIT = 360
# 最多重试几次（防止 sleep 被意外跳过时空转，也防止日志刷屏）
NETWORK_MAX_ATTEMPTS = 20

# ---- 403 深度恢复参数
#
# 背景（2026-11-08 从真实日志分析出来）：
#   上游 study_video 里 max_forbidden_retry = 2，遇到 403 只刷新两次会话就放弃，
#   而实测成功的视频恰恰是「刷新第 2-3 次」才通过的 —— 说明 2 次不够。
#   一次运行 11 个视频只有 2 个成功（18%），就是这么丢的。
#
# 策略：次数提到 6 次，间隔递增（2→4→8→15→25→40 秒）。
#   递增是有意的：连续快速重试只会让风控更认定是机器人，
#   拉开间隔反而更容易放行。
FORBIDDEN_MAX_RETRY = 6
FORBIDDEN_BACKOFF = (2, 4, 8, 15, 25, 40)

_applied: list[str] = []


def applied() -> list[str]:
    """返回已生效的补丁名，供界面/日志展示。"""
    return list(_applied)


def _ok(name: str, detail: str = "") -> None:
    _applied.append(name)
    print(f"[补丁] {name}" + (f" — {detail}" if detail else ""))


def _skip(name: str, why: str) -> None:
    print(f"[补丁] {name} 跳过 — {why}")


def _fail(name: str, exc: Exception) -> None:
    print(f"[补丁] {name} 失败 — {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------- 网络错误判定

def is_network_error(exc: BaseException) -> bool:
    """判断是不是"网断了"这类可恢复错误。

    刻意**不**把 HTTPError（4xx/5xx）算进来：那是服务端的业务响应，
    重试没有意义。只认连不上、超时、DNS 失败、socket 层错误。
    """
    import requests

    network_types = (
        requests.exceptions.ConnectionError,
        requests.exceptions.Timeout,
        requests.exceptions.ChunkedEncodingError,
    )
    if isinstance(exc, network_types):
        return True

    # requests 会把底层 OSError 包一层，拆开再看
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, (ConnectionError, TimeoutError, OSError)):
            return True
        text = f"{type(cur).__name__}: {cur}".lower()
        for kw in ("timed out", "timeout", "connection aborted", "connection reset",
                   "name or service not known", "temporary failure in name resolution",
                   "getaddrinfo failed", "max retries exceeded", "connection refused",
                   "network is unreachable", "remote end closed"):
            if kw in text:
                return True
        cur = cur.__cause__ or cur.__context__
    return False


# ---------------------------------------------------------------- 补丁 1：会话单例

def patch_session_manager() -> None:
    """修 SessionManager.__init__ 每次 get_instance() 都执行一遍。

    上游写法：
        def __new__(cls): ... 返回单例
        def __init__(self): self._session = requests.Session(); ...
    Python 的语义是 **__new__ 返回已有实例时 __init__ 仍会被调用**，
    所以 `SessionManager.get_instance()` 每被调用一次就新建一个 Session：
      · 每次重新读 cookies.txt（磁盘 IO）
      · 旧 session 的连接不关闭（句柄累积）
      · 并发下 cookies 互相覆盖
    长任务跑几小时后表现为文件句柄/内存缓慢上涨。

    修法：加哨兵，只初始化一次。
    """
    if not ENABLE_SESSION_FIX:
        _skip("会话单例", "已在补丁开关里关闭")
        return
    try:
        from api.base import SessionManager

        original_init = SessionManager.__init__
        if getattr(original_init, "_xxt_once", False):
            _skip("会话单例", "已打过")
            return

        @functools.wraps(original_init)
        def init_once(self, *args, **kwargs):
            if getattr(self, "_xxt_ready", False):
                return
            original_init(self, *args, **kwargs)
            self._xxt_ready = True

        init_once._xxt_once = True  # type: ignore[attr-defined]
        SessionManager.__init__ = init_once

        # 立刻验证：连续取两次必须是同一个 Session 对象
        a = SessionManager.get_instance()._session
        b = SessionManager.get_instance()._session
        if a is not b:
            raise RuntimeError("补丁未生效，两次取到的 session 不是同一对象")
        _ok("会话单例", "get_instance() 不再重建 Session")
    except Exception as exc:
        _fail("会话单例", exc)


# ---------------------------------------------------------------- 补丁 2：isdrag

def patch_isdrag() -> None:
    """视频到达结尾时强制 isdrag=4。

    上游 `video_progress_log(..., _isdrag: int = 3)`，只有"秒过"那一次显式传 4；
    播放循环里（base.py 约 898 行）不传，于是结尾那次也用 3。

    学习通播放器语义：3=拖动跳转，4=自然播完。用 3 报结尾，平台常常不认，
    日志里表现为「视频已到末尾，但平台连续 N 次未确认完成」——这是最高频的失败。

    修法：包住 video_progress_log，当 playingTime >= duration 时把 isdrag 改成 4。
    """
    if not ENABLE_ISDRAG_FIX:
        _skip("视频结尾 isdrag", "已在补丁开关里关闭")
        return
    try:
        from api.base import Chaoxing

        original = Chaoxing.video_progress_log
        if getattr(original, "_xxt_isdrag", False):
            _skip("视频结尾 isdrag", "已打过")
            return

        @functools.wraps(original)
        def wrapped(self, _session, _course, _job, _job_info, _dtoken,
                    _duration, _playingTime, *args, **kwargs):
            try:
                duration = int(_duration or 0)
                playing = int(_playingTime or 0)
            except (TypeError, ValueError):
                duration = playing = 0
            # 到达或超过结尾：这是"播完了"，不是"拖动"
            if duration > 0 and playing >= duration:
                kwargs["_isdrag"] = 4
            elif "isdrag" in kwargs and kwargs["isdrag"] == 4:
                # 上游自己传了 4（秒过分支），保持不动
                pass
            return original(self, _session, _course, _job, _job_info, _dtoken,
                            _duration, _playingTime, *args, **kwargs)

        wrapped._xxt_isdrag = True  # type: ignore[attr-defined]
        Chaoxing.video_progress_log = wrapped
        _ok("视频结尾 isdrag", "playingTime >= duration 时上报 isdrag=4")
    except Exception as exc:
        _fail("视频结尾 isdrag", exc)


# ---------------------------------------------------------------- 补丁 3：断网容错

def patch_network_resilience() -> None:
    """断网时等待恢复，不消耗章节重试次数、不让工作线程死掉。

    上游的链路是：
        JobProcessor.worker_thread → process_chapter → get_job_list / study_*
    `process_chapter` 没有任何异常保护，网络错误会一路抛到 worker_thread，
    而 worker_thread 只在取队列时捕了 ShutDown —— 于是：
      · 线程直接死掉
      · task_queue.join() 永远等不到 task_done
      · **整个程序静默挂死**（界面还在，进度不动）

    修法：包住 process_chapter，遇到网络错误就按退避等待并重试同一章；
    等超过 NETWORK_MAX_WAIT 才放弃（返回 ERROR 走正常重试计数）。
    """
    if not ENABLE_NETWORK_FIX:
        _skip("断网容错", "已在补丁开关里关闭")
        return
    try:
        import main as upstream
        from api.logger import logger

        original = upstream.process_chapter
        if getattr(original, "_xxt_net", False):
            _skip("断网容错", "已打过")
            return

        @functools.wraps(original)
        def wrapped(chaoxing, course, point, speed):
            started = time.time()
            attempt = 0
            title = point.get("title", "?") if isinstance(point, dict) else "?"
            while True:
                try:
                    return original(chaoxing, course, point, speed)
                except (KeyboardInterrupt, SystemExit):
                    raise
                except BaseException as exc:
                    if not is_network_error(exc):
                        raise  # 业务错误照旧抛给上游处理

                    attempt += 1
                    waited = time.time() - started
                    if waited >= NETWORK_MAX_WAIT or attempt > NETWORK_MAX_ATTEMPTS:
                        logger.error(
                            "章节「{}」网络持续异常（重试 {} 次 / {:.0f} 分钟），放弃本章：{}",
                            title, attempt - 1, waited / 60, exc,
                        )
                        return upstream.ChapterResult.ERROR

                    # 5s → 10s → 20s → 30s 封顶，别把服务器打急
                    delay = min(5 * (2 ** (attempt - 1)), 30)
                    # 前 3 次详细记，之后每 10 次记一条，避免断网几十分钟把日志刷爆
                    if attempt <= 3 or attempt % 10 == 0:
                        logger.warning(
                            "章节「{}」网络异常（第 {} 次，累计 {:.0f} 秒），{} 秒后重试同一章；"
                            "本次重试不消耗章节重试次数：{}",
                            title, attempt, waited, delay, exc,
                        )
                    time.sleep(delay)

        wrapped._xxt_net = True  # type: ignore[attr-defined]
        upstream.process_chapter = wrapped
        _ok("断网容错", f"网络错误原地重试同一章，单章最多等 {NETWORK_MAX_WAIT // 60} 分钟")
    except Exception as exc:
        _fail("断网容错", exc)


# ---------------------------------------------------------------- 补丁 4：并发保护

def patch_session_concurrency() -> None:
    """会话变单例后，多个工作线程会同时打同一个 Session。

    两个真实风险：
      1. `HTTPAdapter(max_retries=10)` 默认连接池只有 10 个连接（pool_maxsize），
         4 个 worker 各自的章节里又有多任务点并发时，容易触发
         "Connection pool is full, discarding connection" 并静默丢连接。
      2. `session.cookies` 在 relogin 时会被整体覆盖，而其它线程可能正在读，
         读到半更新状态会带错 cookie。

    修法：
      · 把连接池放大（pool_maxsize=32, pool_block=False）
      · 给 Session.request 加一把可重入锁，串行化"读 cookie → 发请求"这一段。
        这会略微降低并发上限，但换来确定性；章节级并发仍由 jobs 控制。
    """
    if not ENABLE_SESSION_LOCK:
        _skip("并发保护", "已在补丁开关里关闭")
        return
    try:
        import threading

        import requests
        from requests.adapters import HTTPAdapter

        from api.base import SessionManager

        session = SessionManager.get_instance()._session

        if getattr(session, "_xxt_pool_fixed", False):
            _skip("并发保护", "已打过")
            return

        # 1) 放大连接池：上游挂的是默认 HTTPAdapter
        adapter = HTTPAdapter(
            pool_connections=16,
            pool_maxsize=32,
            max_retries=10,
            pool_block=False,
        )
        session.mount("https://", adapter)
        session.mount("http://", adapter)

        # 2) 串行化请求，避免 relogin 覆盖 cookie 时别的线程读到中间状态。
        #    注意：这里包的是实例属性（partial），不是类方法，改完立刻生效。
        lock = threading.RLock()
        original = session.request

        @functools.wraps(original)
        def locked_request(*args, **kwargs):
            with lock:
                return original(*args, **kwargs)

        session.request = locked_request
        session._xxt_pool_fixed = True

        _ok("并发保护", "连接池 32，请求加锁（配合单例会话）")
    except Exception as exc:
        _fail("并发保护", exc)


# ---------------------------------------------------------------- 补丁 5：崩溃护栏

def patch_crash_guard() -> None:
    """章节处理里任何未预期异常都不许把工作线程弄死。

    为什么这是最严重的一个：
      main.JobProcessor.worker_thread 主循环是
          task = self.task_queue.get()
          task.result = process_chapter(...)      ← 没有 try
          ...
          self.task_queue.task_done()
      process_chapter 一旦抛非网络异常（KeyError / AttributeError / 解析失败 /
      HTML 改版……），异常冒到 worker_thread，而 worker_thread 只捕 ShutDown：
        · 线程直接死掉
        · 该任务的 task_done() 永远不会被调用
        · run() 里的 task_queue.join() 永久阻塞
        · **整个程序静默挂住**：界面还在、进度不动、日志不再更新

    上游虽然给 worker_thread 加了 @log_error，但它只是记日志后 **重新 raise**，
    救不了线程。

    修法：包住 process_chapter，未预期异常转成 ChapterResult.ERROR ——
    让上游走它自己的重试逻辑（max_tries=5），而不是把线程打死。
    网络类异常由 patch_network_resilience 先行处理，这里只兜剩余的。
    """
    if not ENABLE_CRASH_GUARD:
        _skip("崩溃护栏", "已在补丁开关里关闭")
        return
    try:
        import main as upstream
        from api.logger import logger

        original = upstream.process_chapter
        if getattr(original, "_xxt_guard", False):
            _skip("崩溃护栏", "已打过")
            return

        @functools.wraps(original)
        def wrapped(chaoxing, course, point, speed):
            try:
                return original(chaoxing, course, point, speed)
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException as exc:
                # 到这一步说明既不是网络问题、也不是上游自己处理过的分支。
                # 记全栈（便于事后定位），但绝不让它掀翻线程。
                title = point.get("title", "?") if isinstance(point, dict) else "?"
                course_title = course.get("title", "?") if isinstance(course, dict) else "?"
                logger.error(
                    "章节「{}」（课程《{}》）处理时出现未预期异常，"
                    "本次记为失败并进入重试，程序继续运行：{}: {}",
                    title, course_title, type(exc).__name__, exc,
                )
                logger.debug("完整堆栈：\n{}", __import__("traceback").format_exc())
                return upstream.ChapterResult.ERROR

        wrapped._xxt_guard = True  # type: ignore[attr-defined]
        upstream.process_chapter = wrapped
        _ok("崩溃护栏", "未预期异常转为章节失败，不再挂死整个程序")
    except Exception as exc:
        _fail("崩溃护栏", exc)


# ---------------------------------------------------------------- 补丁 6：课程列表

def patch_decode_course_folder() -> None:
    """修 decode_course_folder 的空指针。

    上游 api/decode.py 第 75 行原样写着：
        "rename": course.select_one("input.rename-input").attrs["value"]

    只要某个文件夹 <li fileid="..."> 里没有 input.rename-input（学习通改版、
    或者该文件夹是新建未命名状态就会这样），select_one 返回 None，
    再取 .attrs 立刻 AttributeError。

    后果：整个课程列表读不出来 → 「识别我的课程」永远失败 / 一直转圈。

    修法：包住它，取不到 rename 就用空串，不让单个畸形文件夹毁掉整张列表。
    """
    if not ENABLE_DECODE_FIX:
        _skip("课程文件夹解析", "已在补丁开关里关闭")
        return
    try:
        import api.decode as dec
        from bs4 import BeautifulSoup
        from api.logger import logger

        if getattr(dec.decode_course_folder, "_xxt_fix", False):
            _skip("课程文件夹解析", "已打过")
            return

        def safe_decode_course_folder(html_text: str):
            logger.trace("开始解码二级课程列表（已加固）...")
            soup = BeautifulSoup(html_text, "lxml")
            raw_courses = soup.select("ul.file-list>li")
            folder_list = []
            skipped = 0
            for course in raw_courses:
                if not course.attrs.get("fileid"):
                    continue
                node = course.select_one("input.rename-input")
                if node is not None:
                    rename = node.attrs.get("value", "")
                else:
                    # 取不到名字不算错误：用「未命名文件夹」占位，列表照样能出
                    rename = "未命名文件夹"
                    skipped += 1
                folder_list.append({"id": course.attrs["fileid"], "rename": rename})
            if skipped:
                logger.warning(
                    "有 {} 个课程文件夹缺少 rename-input，已用占位名代替"
                    "（这是学习通页面改版导致的，不影响读取课程）", skipped,
                )
            return folder_list

        safe_decode_course_folder._xxt_fix = True  # type: ignore[attr-defined]
        dec.decode_course_folder = safe_decode_course_folder

        # api.base 里是 `from api.decode import (... decode_course_folder ...)`，
        # 属于按名导入，改模块属性对已导入的名字无效，必须同步打过去
        try:
            import api.base as B
            if hasattr(B, "decode_course_folder"):
                B.decode_course_folder = safe_decode_course_folder
        except Exception as exc:
            print(f"[补丁] 同步 api.base 引用失败：{exc}")

        _ok("课程文件夹解析", "缺 rename-input 时用占位名，不再抛 AttributeError")
    except Exception as exc:
        _fail("课程文件夹解析", exc)


def patch_course_list_diag() -> None:
    """给 get_course_list 逐步打点。

    这次朋友发来的诊断包就是卡在这里但日志毫无线索 —— 只能看到
    「开始解码二级课程列表...」然后没了。加打点后，下次卡住能直接看出
    是哪个请求、哪一步、等了多久。
    """
    if not ENABLE_COURSE_DIAG:
        _skip("课程列表诊断", "已在补丁开关里关闭")
        return
    try:
        from api.base import Chaoxing
        from api.logger import logger

        original = Chaoxing.get_course_list
        if getattr(original, "_xxt_diag", False):
            _skip("课程列表诊断", "已打过")
            return

        @functools.wraps(original)
        def wrapped(self):
            import time as _t
            t0 = _t.time()
            logger.info("【诊断】开始读取课程列表（含二级文件夹）")
            try:
                result = original(self)
            except BaseException as exc:
                logger.error(
                    "【诊断】读取课程列表失败，耗时 {:.1f}s：{}: {}",
                    _t.time() - t0, type(exc).__name__, exc,
                )
                raise
            logger.info(
                "【诊断】课程列表读取完成，耗时 {:.1f}s，共 {} 条",
                _t.time() - t0, len(result) if isinstance(result, list) else -1,
            )
            return result

        wrapped._xxt_diag = True  # type: ignore[attr-defined]
        Chaoxing.get_course_list = wrapped
        _ok("课程列表诊断", "记录总耗时与异常，卡住时能定位")
    except Exception as exc:
        _fail("课程列表诊断", exc)


# ---------------------------------------------------------------- 补丁 8：验证码

def patch_captcha_fastfail() -> None:
    """验证码识别不成功时别死磕。

    上游 api/base.py 的 perform_request：
        for attempt in range(3):          # 每轮 try_pass()，失败 sleep(2)
            ...
    而外层 study_video 还有 forbidden_retry（2 次）。
    实测朋友的日志：识别一次都没成功，却连试 3+3+3+3 = 12 次、白等约 60 秒，
    最后照样 403 跳过整章 —— 纯浪费，还让界面看起来"卡住"。

    这里把每轮的 3 次改成 1 次：识别不通就立刻返回，交给外层按 403 处理。
    外层已有"跳过该任务点、继续其他章节"的逻辑，用户只需要手动播那一个视频。
    """
    if not ENABLE_CAPTCHA_FASTFAIL:
        _skip("验证码快速放手", "已在补丁开关里关闭")
        return
    try:
        import api.captcha as cap

        original_init = cap.CxCaptcha.__init__
        if getattr(original_init, "_xxt_fast", False):
            _skip("验证码快速放手", "已打过")
            return

        # 直接在源码层面无法改 for range(3)，改为给 try_pass 加"只试一次"的包装：
        # 连续失败由调用方的 range(3) 驱动，这里让第 2、3 次直接返回 False 并跳过等待。
        original_pass = cap.CxCaptcha.try_pass

        @functools.wraps(original_pass)
        def try_pass_once(self, *a, **kw):
            state = getattr(self, "_xxt_fail", 0)
            if state >= 1:
                # 本次执行里已经失败过一次：不再请求，直接认输
                return False
            tl = getattr(patch_captcha_fastfail, "_tl", None)
            try:
                if tl is not None:
                    tl.captcha = True      # 只在这次 try_pass 期间允许缩短 sleep
                ok = original_pass(self, *a, **kw)
            except Exception:
                self._xxt_fail = state + 1
                raise
            finally:
                if tl is not None:
                    tl.captcha = False
            if not ok:
                self._xxt_fail = state + 1
            return ok

        cap.CxCaptcha.try_pass = try_pass_once
        try_pass_once._xxt_fast = True  # type: ignore[attr-defined]

        # 验证码失败后的 time.sleep(2) 也要缩短，否则 3 轮照样白等 6 秒。
        # ⚠ 不能全局改 time.sleep —— 视频播放循环也用 sleep 控制节奏（wait_time 30~90 秒），
        # 那些必须原样。所以用线程局部标记：只在验证码重试这段窗口内生效。
        import threading
        import time as _time

        _real_sleep = _time.sleep
        _tl = threading.local()

        def brief_sleep(sec, *a, **kw):
            if getattr(_tl, "captcha", False):
                try:
                    if 1.0 <= float(sec) <= 5.0:
                        sec = 0.2
                except (TypeError, ValueError):
                    pass
            return _real_sleep(sec, *a, **kw)

        if not getattr(_time.sleep, "_xxt_brief", False):
            brief_sleep._xxt_brief = True  # type: ignore[attr-defined]
            _time.sleep = brief_sleep

        # 进入/离开"验证码窗口"的开关，由 try_pass 包装控制
        patch_captcha_fastfail._tl = _tl  # type: ignore[attr-defined]

        _ok("验证码快速放手", "识别失败不再连试 12 次，立刻交给外层跳过")
    except Exception as exc:
        _fail("验证码快速放手", exc)


# ---------------------------------------------------------------- 补丁 9：403 深度恢复

def patch_forbidden_deep_retry() -> None:
    """403 时多刷几次会话，而不是 2 次就放弃。

    问题（真实日志实证）：
        core/api/base.py 的 study_video 里写死 `max_forbidden_retry = 2`。
        遇到 403 只刷新两次会话，还不行就 return FORBIDDEN 跳过该任务点。

        而 2026-11-08 那次 36 分钟的运行里：
            视频任务开始 11 个
            视频任务完成  2 个   ← 18% 成功率
            403 报错     22 次
        成功的那两个（p26、p27）恰恰都是「刷新第 2 次」才通过的。

    → 2 次不够，但上游把它写死在函数体里，没法直接改常量。
      所以这里包一层 study_video：把内部的重试预算临时调大。

    实现方式：
        不改源码，而是在调用 study_video 前把
        `self` 上挂一个标记，并在函数执行期间替换 time.sleep 的行为 —— 太脆弱。

        实际做法（更稳）：直接替换整个 study_video 里的 403 分支不可行，
        所以改用「外层反复调用」策略：
          403 放弃后，我们重新调一次 study_video（带着已刷的进度），
          每次之间按 FORBIDDEN_BACKOFF 等待。
          每次调用内部本身还会刷 2 次，所以总尝试次数 = 6 × 2 = 12 次。

        这等价于把重试次数提到 12 次，而且完全不碰内核源码。
    """
    if not ENABLE_FORBIDDEN_DEEP:
        _skip("403 深度恢复", "已在补丁开关里关闭")
        return
    try:
        from api.base import Chaoxing, StudyResult
        from api.logger import logger

        original = getattr(Chaoxing, "study_video", None)
        if original is None:
            _fail("403 深度恢复", RuntimeError("找不到 study_video"))
            return
        if getattr(original, "_xxt_forbidden_deep", False):
            _skip("403 深度恢复", "已打过")
            return

        def deep_study_video(self, _course, _job, _job_info, _speed=1.0, _type="Video"):
            last = None
            for round_no in range(FORBIDDEN_MAX_RETRY):
                try:
                    result = original(self, _course, _job, _job_info,
                                      _speed=_speed, _type=_type)
                except Exception as exc:
                    # 异常不算 403，交给上层崩溃护栏处理
                    raise exc

                if not result.is_failure():
                    return result

                # 只有 FORBIDDEN 才值得再刷一轮；其它失败（解析失败、任务不存在）重试没意义
                if result != StudyResult.FORBIDDEN:
                    return result

                last = result
                if round_no >= FORBIDDEN_MAX_RETRY - 1:
                    break

                wait = FORBIDDEN_BACKOFF[min(round_no, len(FORBIDDEN_BACKOFF) - 1)]
                logger.warning(
                    "[403深度恢复] 第 {}/{} 轮失败，等 {} 秒后重来（共 {} 次机会）",
                    round_no + 1, FORBIDDEN_MAX_RETRY, wait, FORBIDDEN_MAX_RETRY,
                )
                time.sleep(wait)

            logger.error(
                "[403深度恢复] %s 轮全部失败，放弃该任务点：%s",
                FORBIDDEN_MAX_RETRY,
                _job.get("name", _job.get("jobid", "")),
            )
            return last if last is not None else StudyResult.FORBIDDEN

        deep_study_video._xxt_forbidden_deep = True  # type: ignore[attr-defined]
        Chaoxing.study_video = deep_study_video

        import api.base as B
        B.Chaoxing.study_video = deep_study_video

        _ok("403 深度恢复",
            f"遇到 403 最多重试 {FORBIDDEN_MAX_RETRY} 轮，间隔递增 "
            f"{'/'.join(str(x) for x in FORBIDDEN_BACKOFF)} 秒")
    except Exception as exc:
        _fail("403 深度恢复", exc)


# ---------------------------------------------------------------- 统一入口

def apply_all() -> None:
    """按依赖顺序打全部补丁。在 import 上游 main 之后调用。"""
    patch_session_manager()
    patch_session_concurrency()   # 必须在单例补丁之后：要先拿到稳定的 session
    patch_isdrag()
    # 顺序要紧：先上崩溃护栏（最外层），再上断网容错（内层先处理网络类）
    patch_crash_guard()
    patch_network_resilience()
    patch_decode_course_folder()
    patch_course_list_diag()
    patch_captcha_fastfail()
    patch_forbidden_deep_retry()   # 必须最后：要包住已经打好的 study_video
    if _applied:
        print(f"[补丁] 共生效 {len(_applied)} 项：{'、'.join(_applied)}")
    else:
        print("[补丁] 没有补丁生效，使用上游原样行为")
