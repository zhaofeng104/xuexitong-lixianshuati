# -*- coding: utf-8 -*-
"""内核启动包装：把上游 main.py 的 stdin 交互改成非交互模式。

上游 main.py 在 course_list 为空时调用 input() 让用户输入课程 ID；
我们从界面启动时 stdin 是 DEVNULL，input() 会抛 EOFError 直接崩。

处理办法：不改上游源码，而是从这里 import 上游的 main 模块后，
把 builtins.input 换成一个"按上下文给合理默认值"的桩：

  - 问课程 ID  → 返回空串，上游 filter_courses 会把空列表当「全部课程」
  - 问账号密码 → 直接报错退出（界面已经保证不会走到这一步）
  - 其他        → 返回空串

这样 core\\ 下的上游代码保持原样，升级时只需替换 core\\ 目录。
"""

from __future__ import annotations

import builtins
import os
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE = os.path.join(ROOT, "core")

sys.path.insert(0, CORE)
os.chdir(CORE)

_real_input = builtins.input


def _patched_input(prompt: str = "") -> str:
    text = str(prompt or "")
    low = text.lower()

    # 课程列表：留空 = 上游课程列表分支里 course_task 为空 -> 走 all_course
    if "课程" in text or "course" in low:
        print(f"{text}（非交互模式：使用全部课程）")
        return ""

    # 账号密码：界面已经填好，走到这里说明配置没落盘
    if "手机号" in text or "密码" in text or "phone" in low or "password" in low:
        print(f"{text}（非交互模式：账号未配置，退出）", file=sys.stderr)
        raise SystemExit(9)

    # 章节检测是否继续之类的 y/n 询问：保守取"继续"
    if text.strip().endswith(("?", "：", ":")) or "确认" in text or "是否" in text:
        print(f"{text}（非交互模式：默认继续）")
        return "y" if "(" in text and "y" in low else ""

    print(f"{text}（非交互模式：跳过）")
    return ""


def _patch_network_timeout() -> None:
    """上游把 requests 超时写死成 5 秒（api/base.py 的 SessionManager）。

    5 秒对首次 TLS 握手偏紧，学校网络或弱网下动辄 ReadTimeout，
    表现就是"明明密码对却登录不上"。这里把默认超时放宽到 20 秒，
    不改 core\\ 里的源码，升级内核时不冲突。

    做法：直接给 requests.Session.request 包一层，逐个调用点注入/放宽 timeout，
    因为上游既有 partial(timeout=5) 也有调用点显式传 timeout=，只改一处不够。
    """
    try:
        import requests

        soft = 20.0
        original = requests.Session.request

        if getattr(original, "_chaoxing_patched", False):
            print("[启动] 网络超时补丁已存在")
            return

        def request_with_timeout(self, method, url, **kwargs):
            current = kwargs.get("timeout")
            if current is None:
                kwargs["timeout"] = soft
            else:
                # 上游写死的 5 秒（含 tuple 形式）一律放宽，但别压过用户显式的大超时
                if isinstance(current, tuple):
                    kwargs["timeout"] = tuple(max(float(x), soft) for x in current)
                elif float(current) <= 6.0:
                    kwargs["timeout"] = soft
            return original(self, method, url, **kwargs)

        request_with_timeout._chaoxing_patched = True  # type: ignore[attr-defined]
        requests.Session.request = request_with_timeout
        print(f"[启动] 网络超时已放宽到 {soft:.0f} 秒")
    except Exception as exc:  # 拿不到就算了，5 秒也能跑
        print(f"[启动] 未能调整网络超时（{exc}），沿用上游默认值")


def _relax_session_partial() -> None:
    """上游 SessionManager.__init__ 把 session.request 换成 partial(timeout=5)。

    我们改的是 requests.Session.request，但实例上的 partial 会绕过它，
    所以导入后再把那个 partial 换成指向已打补丁方法的 partial。
    """
    try:
        import requests
        from api.base import SessionManager

        session = SessionManager.get_instance()._session
        patched = requests.Session.request
        if getattr(session.request, "func", None) is patched:
            return
        import functools

        bound = patched.__get__(session, type(session))
        session.request = functools.partial(bound)
        print("[启动] 已修正 SessionManager 的超时包装")
    except Exception as exc:
        print(f"[启动] 修正 SessionManager 失败（{exc}）")


def main() -> int:
    builtins.input = _patched_input
    # 必须在上游 import 之前打补丁：SessionManager 在实例化时就把
    # session.request 改成了 functools.partial(..., timeout=5)
    _patch_network_timeout()
    try:
        import main as upstream  # noqa: F401  上游 main.py
    except SystemExit as exc:
        return int(exc.code or 0)
    except Exception:
        traceback.print_exc()
        return 1

    _relax_session_partial()

    # 内核缺陷修补（会话单例 / 视频结尾 isdrag / 断网容错）
    try:
        import patches
        patches.apply_all()
    except Exception:
        print("[补丁] 加载失败，以原样行为继续运行：", file=sys.stderr)
        traceback.print_exc()

    # 功能增强（文档翻阅 / 链接任务 / 人脸通知）
    # 必须在 patches 之后、upstream.main() 之前：
    #   · 在 patches 之后 —— 增强层要包在补丁层的最外面，才能同时覆盖
    #   · 在 main() 之前 —— process_job 是模块级函数，运行中再换就没用了
    try:
        import enhance
        enhance.apply_all()
    except Exception:
        print("[增强] 加载失败，以原样行为继续运行：", file=sys.stderr)
        traceback.print_exc()

    try:
        upstream.main()
        return 0
    except SystemExit as exc:
        return int(exc.code or 0)
    except KeyboardInterrupt:
        print("\n[已中断]", file=sys.stderr)
        return 130
    except Exception:
        traceback.print_exc()
        return 1
    finally:
        builtins.input = _real_input


if __name__ == "__main__":
    sys.exit(main())
