# -*- coding: utf-8 -*-
"""一键打包：把内核、界面代码拼成可拷走的绿色目录。

前提：_runtime\\（Python 环境）与 _下载\\src\\chaoxing-main\\（上游源码）已就绪。
用 _runtime\\Scripts\\python.exe app\\build.py 运行。
"""

from __future__ import annotations

import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "_下载", "src", "chaoxing-main")
CORE = os.path.join(ROOT, "core")
RUNTIME = os.path.join(ROOT, "_runtime")

# 上游仓库里对本程序无用的东西
SKIP = {".github", ".gitignore", "tests", "Dockerfile", "app.py",
        "__pycache__", ".pytest_cache", "pyproject.toml"}

# 我们对 core\ 做的**源码级**改动（不是运行时补丁），重建时必须重新打上。
# 目前只有暂停功能这一处，涉及三个文件：
#   api/pause.py  新增文件
#   main.py       章节入口插检查点
#   api/base.py   发包循环里插检查点（关键：必须在计快照之前插，
#                 否则暂停期间时间照走，进度虚高）
# 这些补丁的完整内容备份在 _下载\core_backup_*\ 与 app\core_patches\ 下。
CORE_PATCH_DIR = os.path.join(ROOT, "app", "core_patches")


def main() -> int:
    print("=" * 62)
    print("打包学习通刷课助手")
    print("=" * 62)

    if not os.path.isdir(SRC):
        print(f"\n[失败] 找不到上游源码：{SRC}")
        print("请先下载 Samueli924/chaoxing 源码解压到该位置。")
        return 1

    # 1. 重建 core\
    if os.path.isdir(CORE):
        shutil.rmtree(CORE)
    os.makedirs(CORE)

    copied = 0
    for name in sorted(os.listdir(SRC)):
        if name in SKIP:
            continue
        src_path = os.path.join(SRC, name)
        dst_path = os.path.join(CORE, name)
        if os.path.isdir(src_path):
            shutil.copytree(
                src_path, dst_path,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
            )
        else:
            shutil.copy2(src_path, dst_path)
        copied += 1
    print(f"\n[1] 内核 {copied} 个条目 -> core\\")

    # 1.5 重新打源码补丁
    #
    # ★ 这一步不能省。上面刚把 core\ 整个删掉重建，暂停功能的三个改动
    #   （api/pause.py 新文件、main.py 与 api/base.py 的检查点）全没了。
    #   少了它，界面上的暂停按钮点了不生效，而且不会报错 —— 最难查的那种。
    print("\n[1.5] 重新打源码补丁（暂停 / 继续）")
    import subprocess
    patch_script = os.path.join(ROOT, "app", "apply_core_patches.py")
    if not os.path.isfile(patch_script):
        print(f"  [失败] 找不到 {patch_script}")
        return 1
    cp = subprocess.run(
        [sys.executable, patch_script],
        capture_output=True, cwd=ROOT,
    )
    out = (cp.stdout or b"").decode("utf-8", "replace")
    err = (cp.stderr or b"").decode("utf-8", "replace")
    print(out.rstrip())
    if err.strip():
        print(err.rstrip())
    if cp.returncode != 0:
        print(f"  [失败] 打补丁退出码 {cp.returncode}")
        return 1

    # 2. 校验
    must = [
        ("core/main.py", os.path.join(CORE, "main.py")),
        ("core/api/base.py", os.path.join(CORE, "api", "base.py")),
        ("core/api/pause.py", os.path.join(CORE, "api", "pause.py")),
        ("core/api/answer.py", os.path.join(CORE, "api", "answer.py")),
        ("core/api/decode.py", os.path.join(CORE, "api", "decode.py")),
        ("core/config_template.ini", os.path.join(CORE, "config_template.ini")),
        ("core/resource/font_map_table.json",
         os.path.join(CORE, "resource", "font_map_table.json")),
        ("_runtime/python.exe", os.path.join(RUNTIME, "python.exe")),
        ("_runtime/pythonw.exe", os.path.join(RUNTIME, "pythonw.exe")),
        ("app/ui.py", os.path.join(ROOT, "app", "ui.py")),
        ("app/launch_core.py", os.path.join(ROOT, "app", "launch_core.py")),
        ("app/patches.py", os.path.join(ROOT, "app", "patches.py")),
        ("启动.bat", os.path.join(ROOT, "启动.bat")),
        ("总说明.md", os.path.join(ROOT, "总说明.md")),
    ]
    print("\n[2] 完整性检查")
    ok = True
    for label, path in must:
        exists = os.path.exists(path)
        size = ""
        if exists and os.path.isfile(path):
            size = f"{os.path.getsize(path) / 1024:.0f} KB"
        print(f"  {'OK  ' if exists else 'MISS'}  {label:34} {size}")
        ok = ok and exists

    # 3. 体积统计
    total = 0
    for folder in ("app", "core", "_runtime"):
        path = os.path.join(ROOT, folder)
        for dirpath, _dirnames, filenames in os.walk(path):
            for fn in filenames:
                try:
                    total += os.path.getsize(os.path.join(dirpath, fn))
                except OSError:
                    pass
    print(f"\n[3] 程序总体积：{total / 1024 / 1024:.0f} MB（不含 _下载\\ 与 _参考_B版\\）")

    print("\n" + "=" * 62)
    print("结论：" + ("打包完整，双击 启动.bat 即可" if ok else "有文件缺失，见上面 MISS 项"))
    print("=" * 62)
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
