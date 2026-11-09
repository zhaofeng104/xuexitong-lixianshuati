# -*- coding: utf-8 -*-
"""把暂停功能打进 core\\ —— 重建 core\\ 后必须重跑，否则暂停按钮失效。

用法（由 app\\build.py 自动调用，一般不用手动跑）：
    _runtime\\python.exe app\\apply_core_patches.py

--------------------------------------------------------------------------
为什么要单独一个脚本
--------------------------------------------------------------------------
app\\patches.py 里的 8 项都是**运行时 monkey-patch**，不动源码。
但暂停功能必须在两个具体位置插入代码：

  1. main.py 的 process_chapter() 入口  —— 换章节时能停下
  2. api/base.py 的发包循环里            —— 长视频播放中途能停下

第 2 处尤其关键：必须在 `dt = time.time() - last_iter` **之前**插入，
否则暂停那段时间会被算进观看时长（见 api/pause.py 的说明）。

而 app\\build.py 每次都 rmtree(core) + 整目录复制上游源码，
所以这两个插入会被抹掉。本脚本负责在复制完之后重新打上。

设计原则：**幂等**。跑几次结果都一样，不会重复插入。
检测方式是在插入点上方留一句固定注释，见到就跳过。
"""

from __future__ import annotations

import io
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE = os.path.join(ROOT, "core")
PATCH_SRC = os.path.join(ROOT, "app", "core_patches")

# 用来判断"已经打过了"的哨兵串
SENTINEL_MAIN = "# [暂停补丁] 章节级检查点"
SENTINEL_BASE = "# [暂停补丁] 发包循环检查点"
SENTINEL_DECODE = "def _xxt_course_list_compat("


def read(path: str) -> str:
    with io.open(path, "r", encoding="utf-8") as f:
        return f.read()


def write(path: str, text: str) -> None:
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def say(msg: str = "") -> None:
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        print(msg.encode(enc, "replace").decode(enc, "replace"), flush=True)


def install_pause_module() -> bool:
    """把 api/pause.py 放进 core\\api\\。"""
    dst = os.path.join(CORE, "api", "pause.py")
    src = os.path.join(PATCH_SRC, "api", "pause.py")
    if not os.path.isfile(src):
        say(f"  [失败] 补丁源文件缺失：{src}")
        return False
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(src, dst)
    say(f"  OK    core\\api\\pause.py（{os.path.getsize(dst)} 字节）")
    return True


def patch_main() -> bool:
    """main.py：process_chapter 入口插检查点。"""
    path = os.path.join(CORE, "main.py")
    text = read(path)

    if SENTINEL_MAIN in text:
        say("  SKIP  core\\main.py 已打过补丁")
        return True

    # 1) 补 import
    if "from api.pause import wait_if_paused" not in text:
        anchor = "from api.process import increase_learning_count_for_course"
        if anchor not in text:
            say("  [失败] main.py 找不到 import 锚点")
            return False
        text = text.replace(
            anchor,
            anchor + "\nfrom api.pause import wait_if_paused",
            1,
        )

    # 2) 补检查点
    anchor_fn = "def process_chapter(chaoxing: Chaoxing, course: dict[str, Any], point: dict[str, Any], speed: float) -> ChapterResult:\n"
    if anchor_fn not in text:
        say("  [失败] main.py 找不到 process_chapter 定义")
        return False

    insert = (
        anchor_fn
        + '    """处理单个章节"""\n'
        + f"    {SENTINEL_MAIN}\n"
        + "    # 暂停期间停在这里，时间不计入观看时长（详见 api/pause.py）\n"
        + "    if wait_if_paused(lambda: logger.info(\"已暂停，等待继续…\")):\n"
        + "        logger.info(\"已恢复\")\n"
    )
    old_block = anchor_fn + '    """处理单个章节"""\n'
    if old_block not in text:
        say("  [失败] main.py 的 docstring 与预期不符，拒绝盲改")
        return False

    text = text.replace(old_block, insert, 1)
    write(path, text)
    say("  OK    core\\main.py 插入章节级检查点")
    return True


def patch_base() -> bool:
    """api/base.py：发包循环里插检查点（必须早于 dt 计算）。"""
    path = os.path.join(CORE, "api", "base.py")
    text = read(path)

    if SENTINEL_BASE in text:
        say("  SKIP  core\\api\\base.py 已打过补丁")
        return True

    # 1) 补 import
    #    注意：base.py 用的是**绝对导入**（from api.xxx），不是相对导入。
    #    一开始按相对导入 from .logger 找锚点，当然找不到。
    if "from api.pause import wait_if_paused" not in text:
        anchor = "from api.answer import Tiku, TikuManual"
        if anchor not in text:
            say("  [失败] base.py 找不到 import 锚点")
            return False
        text = text.replace(
            anchor,
            anchor + "\nfrom api.pause import wait_if_paused",
            1,
        )

    # 2) 插检查点：放在 dt 计算之前
    anchor = (
        "                # Uploading the progress takes time, we assume that the video is still playing in the background, this manually calculates the time elapsed\n"
        "                dt = (time.time() - last_iter) * _speed\n"
        "                last_iter = time.time()\n"
    )
    if anchor not in text:
        say("  [失败] base.py 的 dt 计算段与预期不符，拒绝盲改")
        return False

    replacement = (
        f"                {SENTINEL_BASE}\n"
        "                # 关键：必须放在 dt 之前。若放在后面，暂停的这段时间\n"
        "                # 会被算进 play_time（进度虚高，平台判异常）。\n"
        "                _waited = wait_if_paused()\n"
        "                if _waited:\n"
        "                    # 把等待时间补进 last_iter，等价于暂停期间时钟停住\n"
        "                    last_iter += _waited\n"
        + anchor
    )
    text = text.replace(anchor, replacement, 1)
    write(path, text)
    say("  OK    core\\api\\base.py 插入发包循环检查点")
    return True


def patch_decode_course_list() -> bool:
    """api/decode.py：兼容新版课程列表页（不再提供 input.courseId/clazzId）。

    2026-11 实测：学习通的课程列表页改版了。
      旧版：<input class="courseId" value="123"> + <input class="clazzId" value="456">
      新版：参数写在课程链接里
            <a href=".../stucoursemiddle?courseid=123&clazzid=456&cpi=789&ismooc2=1">
            <span class="course-name" title="课程名">

    原解析器只认 input，新版页面会**静默返回空字符串**：
    课程名能读到（所以列表看起来"有课"），
    但 courseId/clazzId 全是空，到刷课阶段才发现跑不起来。

    这里的做法：整段替换 decode_course_list，新版本先从 input 取，
    取不到再从 a[href] 的 query 里取，两条路都走不通才跳过该课。
    """
    path = os.path.join(CORE, "api", "decode.py")
    text = read(path)

    if SENTINEL_DECODE in text:
        say("  SKIP  core\\api\\decode.py 已打过兼容补丁")
        return True

    old_func = '''def decode_course_list(html_text: str) -> List[Dict[str, str]]:
    """
    解析课程列表页面，提取课程信息
    
    Args:
        html_text: 课程列表页面的HTML内容
        
    Returns:
        课程信息列表，每个课程包含id、title、teacher等信息
    """
    logger.trace("开始解码课程列表...")
    soup = BeautifulSoup(html_text, "lxml")
    raw_courses = soup.select("div.course")
    course_list = []

    for course in raw_courses:
        # 跳过未开放课程
        if course.select_one("a.not-open-tip") or course.select_one("div.not-open-tip"):
            continue

        course_detail = {
            "id": course.attrs["id"],
            "info": course.attrs["info"],
            "roleid": course.attrs["roleid"],
            "clazzId": course.select_one("input.clazzId").attrs["value"],
            "courseId": course.select_one("input.courseId").attrs["value"],
            "cpi": re.findall(r"cpi=(.*?)&", course.select_one("a").attrs["href"])[0],
            "title": course.select_one("span.course-name").attrs["title"],
            "desc": course.select_one("p.margint10").attrs["title"] if course.select_one("p.margint10") else "",
            "teacher": course.select_one("p.color3").attrs["title"]
        }
        course_list.append(course_detail)

    return course_list
'''

    if old_func not in text:
        say("  [失败] decode.py 的 decode_course_list 与预期不符，拒绝盲改")
        return False

    new_func = '''def _xxt_course_list_compat(course) -> Optional[Dict[str, str]]:
    """[本程序补丁] 从单个 div.course 里取课程信息，兼容新旧两版页面。

    新版页面不提供 input.courseId / input.clazzId，参数在 a[href] 的 query 里。
    """
    import re as _re
    from urllib.parse import urlparse as _urlparse, parse_qs as _parse_qs

    # 先把链接拿出来（可能是 h3 > a，也可能直接是 div > a）
    link = course.select_one("h3 a[href]") or course.select_one("a[href]")

    def _from_input(cls: str) -> str:
        node = course.select_one(f"input.{cls}")
        if node is not None and node.attrs.get("value") is not None:
            return str(node.attrs["value"]).strip()
        return ""

    def _from_href(*keys: str) -> str:
        if not link:
            return ""
        href = link.attrs.get("href", "")
        try:
            qs = _parse_qs(_urlparse(href).query)
        except Exception:
            return ""
        for k in keys:
            for cand in (k, k.lower(), k.upper()):
                if cand in qs and qs[cand]:
                    return str(qs[cand][0]).strip()
        return ""

    course_id = _from_input("courseId") or _from_href("courseid", "courseId")
    clazz_id = _from_input("clazzId") or _from_href("clazzid", "clazzId")
    cpi = _from_href("cpi")

    # cpi 有时在 input 里
    if not cpi:
        cpi = _from_input("cpi")

    if not course_id or not clazz_id:
        logger.warning(
            "课程缺少 courseId/clazzId，已跳过: title={} courseid={} clazzid={}",
            (course.select_one("span.course-name").attrs.get("title", "?")
             if course.select_one("span.course-name") else "?"),
            course_id, clazz_id,
        )
        return None

    name_node = course.select_one("span.course-name")
    desc_node = course.select_one("p.margint10")
    tch_node = course.select_one("p.color3")

    return {
        "id": course.attrs.get("id", f"course_{course_id}"),
        "info": course.attrs.get("info", ""),
        "roleid": course.attrs.get("roleid", "0"),
        "clazzId": clazz_id,
        "courseId": course_id,
        # 老版本用 a[href] 里的 cpi，新版同理；都取不到就空着，
        # 后续请求里 cpi 并非所有接口都必需
        "cpi": cpi,
        "title": (name_node.attrs.get("title") if name_node else "") or "",
        "desc": (desc_node.attrs.get("title") if desc_node else "") or "",
        "teacher": (tch_node.attrs.get("title") if tch_node else "") or "",
    }


def decode_course_list(html_text: str) -> List[Dict[str, str]]:
    """解析课程列表页面，提取课程信息（兼容新版页面结构）。"""
    logger.trace("开始解码课程列表...")
    soup = BeautifulSoup(html_text, "lxml")
    raw_courses = soup.select("div.course")
    course_list = []

    for course in raw_courses:
        # 跳过未开放课程
        if course.select_one("a.not-open-tip") or course.select_one("div.not-open-tip"):
            continue

        detail = _xxt_course_list_compat(course)
        if detail:
            course_list.append(detail)

    logger.trace("解码完成，共 {} 门课", len(course_list))
    return course_list
'''

    text = text.replace(old_func, new_func, 1)
    write(path, text)
    say("  OK    core\\api\\decode.py 兼容新版课程列表页")
    return True


def main() -> int:
    say("=" * 62)
    say("给 core\\ 打源码补丁（暂停 / 继续）")
    say("=" * 62)

    if not os.path.isdir(CORE):
        say(f"\n[失败] core\\ 不存在：{CORE}")
        return 1

    ok = True
    say("\n[1] 安装 api/pause.py")
    ok = install_pause_module() and ok

    say("\n[2] main.py")
    ok = patch_main() and ok

    say("\n[3] api/base.py")
    ok = patch_base() and ok

    say("\n[4] api/decode.py（兼容新版课程列表页）")
    ok = patch_decode_course_list() and ok

    say("\n" + "=" * 62)
    if ok:
        say("结论：补丁已就位（重启程序后暂停按钮生效）")
    else:
        say("结论：有失败项，见上面 [失败]")
    say("=" * 62)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
