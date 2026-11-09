# -*- coding: utf-8 -*-
"""只读地列出学习通课程，供界面「识别我的课程」按钮调用。

原则：只调用内核已有的接口（login / get_course_list），不改任何业务逻辑，
失败时把内核日志原样带出来，方便定位是密码错还是网络问题。

输出协议（stdout，供 ui.py 解析）：
    COURSE|<课程名>|<courseId>
    其它行原样透传给人看
"""

from __future__ import annotations

import os
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE = os.path.join(ROOT, "core")
APP = os.path.join(ROOT, "app")

sys.path.insert(0, CORE)
sys.path.insert(0, APP)
os.chdir(CORE)

# 「识别课程」是独立子进程，必须自己加载一遍内核补丁 ——
# 否则这个按钮跑的是上游原样逻辑，decode_course_folder 的空指针依旧会崩。
# （上一版诊断包里「已生效补丁：—」就是因为它压根没跑补丁。）
try:
    import patches
    patches.apply_all()
except Exception:
    print("[提示] 内核补丁加载失败，以原样逻辑继续：", file=sys.stderr)
    traceback.print_exc()


def main() -> int:
    try:
        from api.base import Account, Chaoxing
        from api.config import GlobalConst as gc
    except Exception as exc:
        print(f"[失败] 无法加载内核模块：{exc}")
        print("请确认 core\\main.py 与 core\\api\\ 完整存在。")
        return 2

    # 读配置
    import configparser

    cfg = configparser.ConfigParser()
    cfg.read(os.path.join(CORE, "config.ini"), encoding="utf-8")
    if not cfg.has_section("common"):
        print("[失败] config.ini 缺少 [common] 段，请先在界面里保存一次设置。")
        return 2

    use_cookies = str(cfg["common"].get("use_cookies", "false")).strip().lower() == "true"
    username = str(cfg["common"].get("username", "")).strip()
    password = str(cfg["common"].get("password", ""))

    if not use_cookies and (not username or not password or username == "xxx"):
        print("[失败] 还没有填账号密码。")
        return 2

    gc.COOKIES_PATH = os.path.join(CORE, "cookies.txt")

    try:
        account = Account(username, password)
        cx = Chaoxing(account=account)
        state = cx.login(login_with_cookies=use_cookies)
    except Exception as exc:
        print(f"[失败] 登录过程异常：{type(exc).__name__}: {exc}")
        return 3

    if not state.get("status"):
        print(f"[失败] 登录失败：{state.get('msg', '未知原因')}")
        if "验证码" in str(state.get("msg", "")):
            print("提示：账号触发了验证码，建议改用 Cookie 登录。")
        return 4

    try:
        courses = cx.get_course_list()
    except Exception as exc:
        print(f"[失败] 读取课程列表异常：{type(exc).__name__}: {exc}")
        return 5

    if not courses:
        print("[提示] 课程列表是空的：这个账号可能没有在修课程，或学期的课已归档。")
        return 0

    seen: set[str] = set()
    for course in courses:
        cid = str(course.get("courseId", "")).strip()
        title = str(course.get("title", "")).strip()
        if not cid or cid in seen:
            continue
        seen.add(cid)
        print(f"COURSE|{title}|{cid}")

    print(f"[完成] 共 {len(seen)} 门课程。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
