# -*- coding: utf-8 -*-
"""自检：确认打包完的目录能跑起来。双击 调试启动.bat 前先跑它也行。

检查项：
  1. Python 运行时存在且能导入关键依赖
  2. 内核文件齐全
  3. 界面模块能 import（不弹窗）
  4. 尝试 import 内核 api.base（验证依赖装全了）
"""

from __future__ import annotations

import importlib
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE = os.path.join(ROOT, "core")
PYTHON = os.path.join(ROOT, "_runtime", "python.exe")


def check(label: str, fn) -> bool:
    try:
        detail = fn()
        print(f"  OK    {label}" + (f"  {detail}" if detail else ""))
        return True
    except Exception as exc:
        print(f"  FAIL  {label}  ->  {type(exc).__name__}: {exc}")
        return False


def main() -> int:
    print("=" * 62)
    print("学习通刷课助手 · 环境自检")
    print("=" * 62)
    print(f"\n目录: {ROOT}")
    print(f"Python: {sys.version.split()[0]}  ({sys.executable})\n")

    ok = True

    print("[1] 第三方依赖")
    for mod in ["requests", "loguru", "bs4", "lxml", "pyaes", "fontTools",
                "tqdm", "httpx", "urllib3", "chardet", "tenacity"]:
        ok &= check(mod, lambda m=mod: getattr(importlib.import_module(m), "__version__", ""))

    # ddddocr 现在是**可选**依赖，缺失不算失败：
    #   · 它是验证码识别用的，实测识别不了学习通的验证码
    #   · 光它带进来的 onnxruntime + numpy + PIL 就占 84.5 MB
    #   · core/api/captcha.py 用 try/except ImportError 包着，缺了自动降级
    # 所以这里只报告"已装/已移除"，不参与 ok 判定。
    try:
        importlib.import_module("ddddocr")
        print("  OK    ddddocr（验证码识别）  已装")
    except ImportError:
        print("  SKIP  ddddocr（验证码识别）  已移除（正常，不影响刷课）")

    print("\n[2] 内核文件")
    for rel in ["main.py", "api/base.py", "api/answer.py", "api/decode.py",
                "config_template.ini", "resource/font_map_table.json"]:
        path = os.path.join(CORE, rel)
        ok &= check(rel, lambda p=path: f"{os.path.getsize(p) / 1024:.0f} KB"
                    if os.path.exists(p) else (_ for _ in ()).throw(FileNotFoundError(p)))

    print("\n[3] 内核可导入")
    sys.path.insert(0, CORE)
    cwd = os.getcwd()
    os.chdir(CORE)
    try:
        ok &= check("api.base", lambda: __import__("api.base", fromlist=["Chaoxing"]) and "Chaoxing")
        ok &= check("api.answer", lambda: __import__("api.answer", fromlist=["Tiku"]) and "Tiku")
    finally:
        os.chdir(cwd)

    print("\n[4] 界面模块（不启动窗口）")
    sys.path.insert(0, os.path.join(ROOT, "app"))
    os.environ["DISPLAY_CHECK"] = "1"
    ok &= check("ui.py 可解析", _check_ui_syntax)
    ok &= check("iniio.py", lambda: __import__("iniio") and "ok")
    ok &= check("runner.py", lambda: __import__("runner") and "ok")

    print("\n[5] 内核缺陷补丁")
    ok &= check("patches 可导入", lambda: __import__("patches") and "ok")
    ok &= check("补丁生效情况", _check_patches)
    ok &= check("识别课程子进程已加载补丁", _check_list_courses_patch)

    print("\n[6] 功能增强")
    ok &= check("enhance 可导入", lambda: __import__("enhance") and "ok")
    ok &= check("增强生效情况", _check_enhance)

    print("\n" + "=" * 62)
    print("结论：" + ("全部通过，可以双击 启动.bat" if ok else
                     "有失败项（若只有 ddddocr 可忽略），见上面 FAIL 行"))
    print("=" * 62)
    return 0 if ok else 1


def _check_list_courses_patch() -> str:
    """确认「识别课程」子进程也会加载补丁。

    这个检查是补上去的 —— 之前 list_courses.py 是独立进程却没加载补丁，
    结果界面上「识别我的课程」跑的是上游原样逻辑，遇到畸形课程文件夹就崩，
    而主界面因为加载了补丁看起来一切正常。这种"半生效"必须在自检里拦住。
    """
    path = os.path.join(ROOT, "app", "list_courses.py")
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    if "import patches" not in src or "apply_all()" not in src:
        raise RuntimeError("list_courses.py 没有加载补丁，识别课程会走上游原样逻辑")
    return "已加载"


def _check_enhance() -> str:
    """真正应用一遍功能增强，确认挂载成功。

    和 _check_patches 一个思路：不是"能不能 import"，而是"打完是否真的挂上了"。
    增强是 monkey-patch，静默失效很难发现（比如上游改了函数名），
    所以这里逐项断言补丁标记属性。
    """
    cwd = os.getcwd()
    os.chdir(CORE)
    try:
        sys.path.insert(0, CORE)
        sys.path.insert(0, os.path.join(ROOT, "app"))
        import io
        import contextlib

        import enhance

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            enabled = enhance.apply_all()

        if not enabled:
            # 界面里全关掉也是合法状态，不算失败
            return "全部关闭（界面未勾选）"

        import api.base as B
        import api.decode as D
        import main as M

        problems = []
        if "文档自动翻阅" in enabled:
            if not getattr(B.Chaoxing.study_document, "_xxt_enhanced", False):
                problems.append("文档增强没挂上")
        if "链接任务" in enabled:
            if not hasattr(B.Chaoxing, "study_link"):
                problems.append("study_link 不存在")
            if not getattr(D._process_attachment_cards, "_xxt_link_enhanced", False):
                problems.append("decode 层没挂上")
            if not getattr(M.process_job, "_xxt_link_enhanced", False):
                problems.append("process_job 没挂上")
        if "人脸识别通知" in enabled:
            if not getattr(B.Chaoxing.video_progress_log, "_xxt_face_enhanced", False):
                problems.append("人脸检测没挂上")

        if problems:
            raise RuntimeError("；".join(problems))
        return f"{len(enabled)} 项：{'、'.join(enabled)}"
    finally:
        os.chdir(cwd)


def _check_patches() -> str:
    """真正打一遍补丁，确认它们在当前内核版本上仍然有效。

    这不是"能不能 import"，而是"打完是否真的改变了行为"——
    内核升级后上游可能自己修了，补丁就该跳过；也可能是上游改了函数名，
    补丁静默失效。后者必须让人看见。
    """
    cwd = os.getcwd()
    os.chdir(CORE)
    try:
        sys.path.insert(0, CORE)
        sys.path.insert(0, os.path.join(ROOT, "app"))
        import patches

        # 静音补丁自己的 print，只取结果
        import io
        import contextlib

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            patches.apply_all()
        applied = patches.applied()

        if not applied:
            raise RuntimeError("所有补丁都被跳过 —— core\\ 内核版本可能已变，需要复核")

        # 会话单例是否真的生效（最容易验证的一个）
        from api.base import SessionManager
        s1 = SessionManager.get_instance()._session
        s2 = SessionManager.get_instance()._session
        if s1 is not s2:
            raise RuntimeError("会话单例补丁报告生效，但实测两次取到的不是同一对象")

        return f"{len(applied)} 项：" + "、".join(applied)
    finally:
        os.chdir(cwd)


def _check_ui_syntax() -> str:
    path = os.path.join(ROOT, "app", "ui.py")
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    compile(src, path, "exec")
    return f"{len(src.splitlines())} 行"


if __name__ == "__main__":
    sys.exit(main())
