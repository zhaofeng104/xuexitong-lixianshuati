# -*- coding: utf-8 -*-
"""一键完成构建 + 自检 + 打包成可拷贝的交付压缩包。

给昭逢用：双击 `制作交付包.bat` 就会跑这个脚本。
做的事（全部在 `学习通刷课助手\\` 内，不碰系统）：
  1. 从 _下载\\src 重建 core\\
  2. 跑环境自检（缺 ddddocr 只警告，不中断）
  3. 把可交付的部分压成一个 zip：app / core / _runtime / *.bat / 总说明.md
  4. 打印体积与文件清单

刻意**不**包含：_下载\\（构建中间产物）、config.ini / cookies.txt（隐私）、chaoxing.log。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON = os.path.join(ROOT, "_runtime", "python.exe")
OUT_DIR = os.path.join(ROOT, "_交付")
DIST_NAME = "学习通刷课助手"

# 把 stdout/stderr 强制成 UTF-8：Windows 控制台默认 GBK，
# 我们的日志里全是中文路径，不换编码迟早撞 UnicodeEncodeError。
# Python 3.7+ 支持 reconfigure；失败也不影响（say() 里还有兜底）。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 要打进交付包的东西（相对 ROOT）
INCLUDE_DIRS = [
    "app",
    "core",
    "_runtime",
]
INCLUDE_FILES = [
    "总说明.md",   # 入口文档：先看这个，决定用哪个方案
    "学习通刷课助手.exe",   # 主入口：双击这个，不再是 bat
    "启动.bat",             # exe 被拦截时的退路
    "调试启动.bat",
    "环境自检.bat",
    "关闭程序.bat",          # 一键关闭所有相关进程
]

# 打进包时必须剔除的（隐私 / 缓存 / 体积）
EXCLUDE_PATTERNS = [
    "__pycache__", "*.pyc", ".pytest_cache",
    "config.ini",          # 含明文账号密码
    "cookies.txt",         # 登录凭据
    "chaoxing.log",        # 含运行细节
    "cache.json",          # 答题缓存
    "_patch_check.py",     # 验收脚本，不需要交付
    "_net_check.py",
    "launcher.cs",         # 启动器源码，交付包里只要 exe
    "build_exe.bat",       # 编译脚本，本机用
]


def say(msg: str = "") -> None:
    """打印一行，**永不因编码问题中断**。

    踩过的坑：Windows 控制台是 GBK 代码页，而我们的文本里有中文路径、
    csc 的输出里还混着解码失败的 U+FFFD 替换字符 —— print() 会直接抛
    UnicodeEncodeError，把整个打包流程打断在第 2 步。
    这里显式转码，编不动的字符换成 ? 而不是让程序死掉。
    """
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        safe = msg.encode(enc, "replace").decode(enc, "replace")
        print(safe, flush=True)
    except Exception:
        # 连 stdout 都不可用（被重定向到奇怪的地方）时，绝不阻断主流程
        pass


def step(n: int, total: int, title: str) -> None:
    say("")
    say("=" * 66)
    say(f"  [{n}/{total}] {title}")
    say("=" * 66)


def run(cmd: list[str], cwd: str | None = None, hide_window: bool = True) -> tuple[int, str]:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    # 踩过的坑：给 csc 加 CREATE_NO_WINDOW 会让它生成 Win32 资源时
    # 报 CS1567「拒绝访问」—— 编译器需要为默认图标创建临时资源文件，
    # 在无窗口/受限的进程创建标志下拿不到所需权限。
    # 交互式工具（本脚本）本来就跑在控制台里，不需要隐藏窗口。
    flags = 0
    if hide_window and sys.platform == "win32":
        flags = subprocess.CREATE_NO_WINDOW

    cp = subprocess.run(
        cmd, cwd=cwd or ROOT, capture_output=True, env=env,
        creationflags=flags,
    )
    # 先严格 UTF-8 解，失败说明是 GBK 或含替换字符，回退到 replace 保底
    def _dec(b: bytes) -> str:
        if not b:
            return ""
        try:
            return b.decode("utf-8")
        except UnicodeDecodeError:
            try:
                return b.decode("gbk", "replace")
            except Exception:
                return b.decode("utf-8", "replace")

    return cp.returncode, (_dec(cp.stdout) + _dec(cp.stderr))


def main() -> int:
    total = 5
    say("学习通刷课助手 · 制作交付包")
    say(f"工作目录：{ROOT}")

    if not os.path.exists(PYTHON):
        say(f"\n[失败] 找不到运行时：{PYTHON}")
        say("请先确认 _runtime\\python.exe 存在。")
        return 1

    # 1. 重建 core
    step(1, total, "重建 core\\（从 _下载\\src 复制上游源码）")
    code, out = run([PYTHON, os.path.join(ROOT, "app", "build.py")])
    say(out.rstrip())
    if code != 0:
        say(f"\n[失败] build.py 退出码 {code}，终止。")
        return 1

    # 2. 编译启动器 exe
    #
    # ★ 这一步刻意不用 Python 调 csc。
    #   实测结论（2026-11-06）：
    #     · 在 PowerShell 里直接跑 csc           → exit 0，正常出 exe
    #     · 由 Python subprocess 调 csc          → CS1567「生成 Win32 资源时出错：拒绝访问」
    #     · 由 Python 调 PowerShell 再调 csc     → 同样 CS1567
    #   只要进程链上出现 Python，csc 写默认 Win32 资源就失败。
    #   改 creationflags、换 cwd、换输出目录、换 TEMP 位置全都无效。
    #   所以这里只做"检查 exe 在不在"，真正的编译交给 build_exe.bat
    #   （双击即可，或者由 制作交付包.bat 在调用本脚本前先跑一遍）。
    step(2, total, "检查启动器 exe")
    exe_path = os.path.join(ROOT, "学习通刷课助手.exe")
    if not os.path.isfile(exe_path):
        say(f"  [缺失] {exe_path}")
        say("  先双击 app\\build_exe.bat 编译一次，再回来重跑本脚本。")
        return 1
    exe_size = os.path.getsize(exe_path)
    if exe_size < 1024:
        say(f"  [异常] exe 只有 {exe_size} 字节，像是编译失败的残留。")
        say("  重新双击 app\\build_exe.bat。")
        return 1
    say(f"  OK  {exe_path}（{exe_size} 字节）")

    # 3. 自检
    step(3, total, "环境自检")
    code, out = run([PYTHON, os.path.join(ROOT, "app", "check_env.py")])
    say(out.rstrip())
    dddd_missing = "ddddocr" in out and "FAIL" in out
    if code != 0 and not dddd_missing:
        say(f"\n[失败] 自检有非 ddddocr 的失败项，终止。")
        return 1
    if dddd_missing:
        say("\n[提示] ddddocr 未安装 —— 只影响滑块验证码识别，其余功能正常。")

    # 4. 打包
    step(4, total, "打包")
    if os.path.isdir(OUT_DIR):
        shutil.rmtree(OUT_DIR, ignore_errors=True)
    os.makedirs(OUT_DIR, exist_ok=True)
    zip_path = os.path.join(OUT_DIR, f"{DIST_NAME}.zip")

    added = 0
    skipped = 0
    total_bytes = 0

    def should_skip(name: str) -> bool:
        for pat in EXCLUDE_PATTERNS:
            if pat.startswith("*"):
                if name.endswith(pat[1:]):
                    return True
            elif name == pat:
                return True
        return False

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        # 目录
        for d in INCLUDE_DIRS:
            src_dir = os.path.join(ROOT, d)
            if not os.path.isdir(src_dir):
                say(f"  [跳过] 目录不存在：{d}")
                continue
            for dirpath, dirnames, filenames in os.walk(src_dir):
                dirnames[:] = [x for x in dirnames if not should_skip(x)]
                for fn in filenames:
                    if should_skip(fn):
                        skipped += 1
                        continue
                    full = os.path.join(dirpath, fn)
                    rel = os.path.relpath(full, ROOT)
                    try:
                        zf.write(full, os.path.join(DIST_NAME, rel))
                        added += 1
                        total_bytes += os.path.getsize(full)
                    except OSError as exc:
                        say(f"  [警告] 跳过 {rel}：{exc}")
        # 散文件
        for f in INCLUDE_FILES:
            full = os.path.join(ROOT, f)
            if not os.path.isfile(full):
                say(f"  [跳过] 文件不存在：{f}")
                continue
            zf.write(full, os.path.join(DIST_NAME, f))
            added += 1
            total_bytes += os.path.getsize(full)

    # 5. 核对包内容
    step(5, total, "核对交付包")
    with zipfile.ZipFile(zip_path) as zf:
        bad = zf.testzip()
        names = zf.namelist()
        size_mb = os.path.getsize(zip_path) / 1024 / 1024

    say(f"  文件数：{added}（跳过隐私/缓存 {skipped} 个）")
    say(f"  原始体积：{total_bytes / 1024 / 1024:.1f} MB")
    say(f"  压缩后：{size_mb:.1f} MB")
    say(f"  完整性：{'OK' if bad is None else '损坏 @ ' + str(bad)}")

    must = [
        "学习通刷课助手.exe", "启动.bat", "关闭程序.bat",
        "总说明.md",
        "core/main.py", "core/api/base.py", "core/api/pause.py",
        "core/config_template.ini",
        "app/ui.py", "app/launch_core.py", "app/patches.py", "app/close_app.py",
        "app/enhance.py", "app/enhance_flags.json",
        "_runtime/python.exe", "_runtime/pythonw.exe",
    ]
    say("")
    say("  关键文件：")
    all_ok = True
    for m in must:
        target = f"{DIST_NAME}/{m}".replace("\\", "/")
        present = target in names or any(n.startswith(target) for n in names)
        # _runtime/python.exe 在 zip 里是精确路径
        if m == "_runtime/python.exe":
            present = target in names
        say(f"    {'OK  ' if present else 'MISS'}  {m}")
        all_ok = all_ok and present

    # 隐私兜底：确认没把 config.ini / cookies 打进去
    say("")
    say("  隐私检查（这些不该出现在包里）：")
    leaks = [n for n in names if n.endswith(("config.ini", "cookies.txt", "chaoxing.log", "cache.json"))]
    if leaks:
        say("    [警告] 发现敏感文件：" + ", ".join(leaks[:5]))
        all_ok = False
    else:
        say("    OK  无 config.ini / cookies.txt / 日志 / 缓存")

    say("")
    say("=" * 66)
    if bad is None and all_ok:
        say(f"  交付包已生成：{zip_path}")
        say(f"  把这个 zip 发给朋友，解压后双击「学习通刷课助手.exe」即可。")
        say(f"  （exe 打不开时退回双击「启动.bat」，效果一样）")
    else:
        say("  有问题，见上面警告。")
    say("=" * 66)
    return 0 if (bad is None and all_ok) else 2


if __name__ == "__main__":
    sys.exit(main())
