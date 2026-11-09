# -*- coding: utf-8 -*-
"""暂停 / 继续 —— 用标记文件让内核在检查点停下来。

为什么不用「挂起进程」：
    上游的观看时长是这样算的：``play_time = min(duration, play_time + dt)``，
    其中 ``dt`` 是 **真实时钟** 的流逝量。如果把进程 Suspend 掉十分钟再恢复，
    这十分钟照样会被算进 play_time —— 上报的进度瞬间虚高，
    平台一看就异常（视频才 20 分钟，你 5 分钟就报完了）。
    所以这里改成「在代码里等」：等的时候不计入 dt，时长与实际观看一致。

工作方式：
    UI 侧 ``request_pause()`` 创建一个标记文件；
    内核在检查点调 ``wait_if_paused()``，发现标记就原地 sleep 轮询；
    UI 侧 ``clear_pause()`` 删掉标记，内核下一轮轮询就继续跑。

标记文件路径优先级：
    1. 环境变量 ``CHAOXING_PAUSE_FILE``（由 runner.py 注入，保证 UI 与内核看到同一个文件）
    2. 项目根下的 ``pause.flag``（独立跑内核时的兜底）
"""

from __future__ import annotations

import os
import time
from typing import Callable, Optional

# 轮询间隔。0.5 秒足够灵敏，又不至于空转吃 CPU。
_POLL_INTERVAL = 0.5

# 由 set_pause_file() 覆盖；None 表示还没定过，按环境变量/默认值算
_pause_file: Optional[str] = None


def _default_pause_file() -> str:
    """没显式设过时，按环境变量推到默认路径。"""
    env = os.environ.get("CHAOXING_PAUSE_FILE", "").strip()
    if env:
        return env
    # __file__ = <root>\core\api\pause.py → 上三级是 <root>
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(here))
    return os.path.join(root, "pause.flag")


def set_pause_file(path: str) -> None:
    """显式指定标记文件路径（UI 侧启动子进程时调用）。"""
    global _pause_file
    _pause_file = path


def get_pause_file() -> str:
    global _pause_file
    if _pause_file is None:
        _pause_file = _default_pause_file()
    return _pause_file


def request_pause() -> bool:
    """请求暂停：创建标记文件。已存在则返回 False。"""
    path = get_pause_file()
    if os.path.exists(path):
        return False
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(str(int(time.time())))
        return True
    except OSError:
        return False


def clear_pause() -> bool:
    """取消暂停：删除标记文件。本来就没暂停则返回 False。"""
    path = get_pause_file()
    try:
        if os.path.exists(path):
            os.remove(path)
            return True
    except OSError:
        pass
    return False


def is_paused() -> bool:
    return os.path.exists(get_pause_file())


def wait_if_paused(on_wait: Optional[Callable[[], None]] = None) -> float:
    """如果处于暂停状态，就原地等到恢复。

    返回实际等待的秒数（没暂停返回 0.0）。
    调用方可以用返回值把等待时间从计时里扣掉 —— 不过更稳的做法是
    调用方在**取时间快照之前**就调用本函数，这样根本不用扣。
    """
    if not is_paused():
        return 0.0

    if on_wait is not None:
        try:
            on_wait()
        except Exception:
            # 提示回调出错不能影响暂停本身
            pass

    started = time.time()
    while is_paused():
        time.sleep(_POLL_INTERVAL)
    return time.time() - started
