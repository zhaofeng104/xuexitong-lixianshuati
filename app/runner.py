# -*- coding: utf-8 -*-
"""在后台线程里跑上游 main.py，并把 loguru / tqdm 的输出转成界面日志。

设计要点：
1. 用 multiprocessing 的 spawn 子进程跑上游，父进程崩溃不影响任务清理；
   但在 Windows 上打包后 spawn 会重复执行主模块，所以改为 subprocess 调 python -m main。
2. 子进程 stdout/stderr 用无缓冲读取，逐行投递给回调。
3. 停止 = 终止进程树（上游是多线程，SIGTERM 在 Windows 上是强杀，正好）。
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from typing import Callable, Optional


class Runner:
    """一次任务运行。"""

    def __init__(
        self,
        python_exe: str,
        cwd: str,
        config_path: str,
        on_line: Callable[[str], None],
        on_exit: Callable[[int], None],
    ) -> None:
        self.python_exe = python_exe
        self.cwd = cwd
        self.config_path = config_path
        self.on_line = on_line
        self.on_exit = on_exit
        self.proc: Optional[subprocess.Popen] = None
        self._reader: Optional[threading.Thread] = None
        self._stopping = False
        self.pause_flag: str = ""

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self) -> None:
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        env["PYTHONPATH"] = self.cwd
        env["TQDM_DISABLE"] = "0"

        # 暂停/继续靠标记文件在父子进程间传信号（见 core/api/pause.py）。
        # 界面写这个文件，内核轮询它。路径必须显式传下去，
        # 否则两边各自算默认值会对不上。
        self.pause_flag = os.path.join(os.path.dirname(self.cwd), "pause.flag")
        env["CHAOXING_PAUSE_FILE"] = self.pause_flag
        # 启动前先清掉可能残留的暂停标记，避免一启动就卡住
        try:
            if os.path.exists(self.pause_flag):
                os.remove(self.pause_flag)
        except OSError:
            pass

        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NO_WINDOW  # 不弹黑窗
            if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
                creationflags |= subprocess.CREATE_NEW_PROCESS_GROUP

        # 走 launch_core.py（把上游的 input() 变非交互），而不是直接跑 main.py
        launcher = os.path.join(os.path.dirname(os.path.abspath(__file__)), "launch_core.py")
        self.proc = subprocess.Popen(
            [self.python_exe, launcher, "-c", self.config_path],
            cwd=self.cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            env=env,
            creationflags=creationflags,
            bufsize=0,
        )
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()

    def _pump(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
        stream = self.proc.stdout
        buffer = b""
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    break
                buffer += chunk
                while b"\n" in buffer:
                    raw, buffer = buffer.split(b"\n", 1)
                    self._emit(raw)
                # 进度条用 \r 刷新，也当作行切分
                if b"\r" in buffer:
                    parts = buffer.split(b"\r")
                    buffer = parts.pop()
                    for part in parts:
                        if part.strip():
                            self._emit(part)
            if buffer.strip():
                self._emit(buffer)
        except Exception as exc:  # 读取中断：进程被强杀时会走到这里
            self.on_line(f"[读取异常] {exc}")
        finally:
            code = self.proc.wait() if self.proc else -1
            if not self._stopping:
                self.on_exit(code if code is not None else -1)

    def _emit(self, raw: bytes) -> None:
        # 先把 ESC 字符整个去掉，再收尾；否则 loguru 的 [32m...[0m 会残留在日志里
        line = raw.decode("utf-8", errors="replace")
        line = _strip_ansi(line).replace("\x1b", "").rstrip()
        if line:
            self.on_line(line)

    def stop(self, timeout: float = 8.0) -> None:
        self._stopping = True
        proc = self.proc
        if proc is None or proc.poll() is not None:
            return
        try:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            else:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        deadline = time.time() + timeout
        while time.time() < deadline and proc.poll() is None:
            time.sleep(0.1)


def _strip_ansi(text: str) -> str:
    """去掉 CSI 序列（ESC [ ... 终止符）与残留的 [32m 这类孤立的 SGR 片段。"""
    out = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\x1b" and i + 1 < n and text[i + 1] == "[":
            i += 2
            while i < n and not ("@" <= text[i] <= "~"):
                i += 1
            i += 1
            continue
        out.append(ch)
        i += 1
    # 管道传输偶尔会把 ESC 丢掉，只剩 [32m / [1m / [0m
    return _ORPHAN_SGR.sub("", "".join(out))


_ORPHAN_SGR = __import__("re").compile(r"\[[0-9;]{1,6}m")
