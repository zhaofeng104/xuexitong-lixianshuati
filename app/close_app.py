# -*- coding: utf-8 -*-
"""关闭程序 —— 用 Python 写，比 bat 的字符串匹配可靠得多。

为什么不用 bat：
    bat 版靠 powershell 里判断 ExecutablePath -like '%HERE%*'，
    HERE 路径含中文时，cmd 变量展开与 PowerShell 的引号处理会打架，
    实测出现「明明有进程在跑却报没有找到」的假阴性。
    改用 Python 直接拿 psutil 之外的系统信息（WMI/CIM），路径匹配用 Python 做，
    不受 cmd 转义影响。

规则：
    只杀「可执行文件路径在本目录下」或「命令行里含本目录」的 python/pythonw。
    绝不按进程名一刀切 —— 机器上还有 DSH、其他脚本在用 Python。

用法：
    双击 关闭程序.bat（它会调用本脚本）
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT_LOWER = ROOT.lower()


def say(msg: str = "") -> None:
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        print(msg.encode(enc, "replace").decode(enc, "replace"), flush=True)


def _run_ps(script: str) -> str:
    """跑一段 PowerShell，返回 stdout 文本。

    ★ 编码是这个函数的关键。踩过的坑：
        Python 用 subprocess 抓 PowerShell 输出时，PS 默认按**系统代码页（GBK）**
        往管道里写。而我们用 UTF-8 解码 → 中文字符全变成替换符，
        「D:\\deepseek\\Deepseek工作站\\学习通刷课助手」变成
        「D:\\deepseek\\Deepseek???\\???????」→ 路径匹配永远失败
        → 表现为"明明有进程在跑，脚本却报没有找到"。

    解法：在脚本开头强制把输出编码设成 UTF-8，并要求 BS 侧也用 UTF-8 写管道。
    """
    prefix = (
        "$ProgressPreference='SilentlyContinue'; "
        "try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}; "
        "$OutputEncoding = [System.Text.Encoding]::UTF8; "
    )
    try:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        cp = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-Command", prefix + script],
            capture_output=True, timeout=60, env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        # 先按 UTF-8 严格解，失败退回 GBK（双保险）
        def dec(b: bytes) -> str:
            if not b:
                return ""
            try:
                return b.decode("utf-8")
            except UnicodeDecodeError:
                try:
                    return b.decode("gbk", "replace")
                except Exception:
                    return b.decode("utf-8", "replace")

        return dec(cp.stdout) + dec(cp.stderr)
    except Exception as exc:
        return f"__ERROR__ {exc}"


def find_our_processes() -> list[dict]:
    """列出属于本目录的 Python 进程。"""
    # 一行一个，用 | 分隔，避免 JSON 解析在中文路径下出岔子
    ps = (
        "$ErrorActionPreference='SilentlyContinue'; "
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" | "
        "ForEach-Object { "
        "  $p = if ($_.ExecutablePath) { $_.ExecutablePath } else { '' };"
        "  $c = if ($_.CommandLine) { $_.CommandLine } else { '' };"
        "  \"$($_.ProcessId)|$($_.Name)|$p|$c\" "
        "}"
    )
    text = _run_ps(ps)
    if text.startswith("__ERROR__"):
        say(f"  [警告] 查询进程失败：{text}")
        return []

    result: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue
        parts = line.split("|", 3)
        if len(parts) < 4:
            continue
        pid_s, name, exe, cmd = parts
        try:
            pid = int(pid_s)
        except ValueError:
            continue

        # 过滤：可执行文件路径或命令行里必须出现本目录
        exe_l = exe.lower().replace("/", "\\")
        cmd_l = cmd.lower().replace("/", "\\")
        hit = ROOT_LOWER in exe_l or ROOT_LOWER in cmd_l
        if not hit:
            continue

        # 别把自己（本脚本）算进去
        if pid == os.getpid():
            continue

        result.append({"pid": pid, "name": name, "exe": exe, "cmd": cmd})
    return result


def kill(pid: int) -> bool:
    """强杀一个进程树。"""
    try:
        cp = subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return cp.returncode == 0
    except Exception:
        return False


def main() -> int:
    say("=" * 64)
    say("  关闭 学习通刷课助手")
    say("=" * 64)
    say(f"  目录: {ROOT}")
    say()

    say("[1] 查找本目录启动的进程…")
    procs = find_our_processes()
    if not procs:
        say("    没有找到运行中的实例。")
    else:
        for p in procs:
            say(f"    PID {p['pid']:<8} {p['name']}")
            short = p["cmd"][:90] if p["cmd"] else p["exe"]
            say(f"             {short}")

    say()
    say("[2] 终止进程…")
    killed = 0
    failed = 0
    for p in procs:
        if kill(p["pid"]):
            say(f"    ✓ PID {p['pid']} 已终止")
            killed += 1
        else:
            say(f"    ✗ PID {p['pid']} 终止失败（可能已退出或被保护）")
            failed += 1
    if not procs:
        say("    跳过（无进程）")

    say()
    say("[3] 清理暂停标记…")
    flag = os.path.join(ROOT, "pause.flag")
    if os.path.exists(flag):
        try:
            os.remove(flag)
            say("    ✓ 已删除 pause.flag")
        except OSError as exc:
            say(f"    ✗ 删除失败：{exc}")
    else:
        say("    无需清理")

    say()
    say("[4] 结果复查…")
    time.sleep(0.8)
    left = find_our_processes()
    if left:
        say(f"    仍有 {len(left)} 个进程存活：")
        for p in left:
            say(f"      PID {p['pid']}  {p['name']}")
        say("    这些可能被系统占用，重启电脑后可完全清理。")
    else:
        say("    ✓ 已全部关闭")

    say()
    say("=" * 64)
    if procs and failed == 0:
        say(f"  完成：终止了 {killed} 个进程。")
    elif not procs:
        say("  完成：本来就没有在运行。")
    else:
        say(f"  完成：终止 {killed} 个，失败 {failed} 个。")
    say("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
