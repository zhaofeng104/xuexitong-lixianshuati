# -*- coding: utf-8 -*-
"""增强任务类型支持 —— 文档自动翻阅、链接任务、人脸识别通知。

【为什么单独一个模块，而不是直接改 core/】
    core/ 是上游 GPL-3.0 代码，能不动就不动（动多了升级会冲突）。
    这些功能可以用**运行时 monkey-patch** 实现，跟 app/patches.py 一个思路。
    只有确实做不到的（比如要改函数内部流程），才去动 core/。

【实现的三件事】
    1. 文档任务真正翻阅
       上游 study_document 只发一个 GET 就返回，PPT/PDF 章节不会标记完成。
       这里补上「打开文档页 → 上报浏览进度」两步。

    2. 链接任务自动完成
       上游遇到 attachments 里 type=link 的任务，会走到 "未知任务类型"，
       直接返回 ERROR，导致该知识点永远解锁不了。
       这里补上 link 类型的处理：访问链接 + 上报完成。

    3. 人脸识别通知
       学习通播放中途会弹人脸校验。上游不识别，会一直空等或直接失败。
       这里在视频循环里检测到人脸校验特征时，推一条通知，让用户能及时处理。
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Optional

from api.logger import logger

# ---------------------------------------------------------------- 开关
#
# 这三个值会被 app\enhance_flags.json 覆盖（界面里勾选时写入）。
# 文件不存在时用下面的默认值 —— 默认全开，因为三项都是"不开就卡住"的类型。

ENABLE_DOCUMENT_TURN = True   # 文档自动翻阅
ENABLE_LINK_TASK = True       # 链接任务自动完成
ENABLE_FACE_NOTIFY = True     # 人脸识别通知
ENABLE_CAPTCHA_NOTIFY = True  # 验证码拦截通知


def _load_flags() -> None:
    """从 enhance_flags.json 读开关，覆盖模块默认值。

    为什么用独立小文件而不是 config.ini：
        config.ini 的字段由上游 core/api/config.py 解析，
        未知字段会被忽略，塞不进去。
    """
    global ENABLE_DOCUMENT_TURN, ENABLE_LINK_TASK, ENABLE_FACE_NOTIFY, ENABLE_CAPTCHA_NOTIFY

    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "enhance_flags.json")
    if not os.path.isfile(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            flags = json.load(f)
        if "document_turn" in flags:
            ENABLE_DOCUMENT_TURN = bool(flags["document_turn"])
        if "link_task" in flags:
            ENABLE_LINK_TASK = bool(flags["link_task"])
        if "face_notify" in flags:
            ENABLE_FACE_NOTIFY = bool(flags["face_notify"])

        if "captcha_notify" in flags:
            ENABLE_CAPTCHA_NOTIFY = bool(flags["captcha_notify"])
        logger.debug(
            f"[增强] 开关已加载：文档={ENABLE_DOCUMENT_TURN} "
            f"链接={ENABLE_LINK_TASK} 人脸={ENABLE_FACE_NOTIFY}"
        )
    except Exception as exc:
        logger.debug(f"[增强] 读取开关失败，用默认值：{exc}")


# ---------------------------------------------------------------- 工具

def _get_session():
    from api.base import SessionManager
    return SessionManager.get_session()


def _job_knowledge_id(job: dict) -> str:
    """从 otherinfo 里抠 knowledgeId（nodeId_xxx-yyy 格式）。"""
    info = str(job.get("otherinfo") or job.get("otherInfo") or "")
    m = re.search(r"nodeId_(\d+)", info)
    return m.group(1) if m else ""


def _has_face_check(text: str) -> bool:
    """判断页面/响应里有没有人脸校验特征。

    学习通的人脸校验会在响应里带这些关键字之一：
      faceCapture / videoFaceCapture / 人脸 / faceDetect / captureEnc
    """
    if not text:
        return False
    keys = ("videoFaceCapture", "faceCapture", "faceDetect",
            "人脸识别", "人脸校验", "facecheck")
    low = text
    return any(k in low for k in keys)


# ---------------------------------------------------------------- 1. 文档任务

def _patch_document() -> bool:
    """增强 study_document：发完基础请求后，再模拟打开文档页面。"""
    try:
        from api.base import Chaoxing
    except Exception as exc:
        logger.warning(f"[增强] 无法导入 Chaoxing：{exc}")
        return False

    original = getattr(Chaoxing, "study_document", None)
    if original is None:
        logger.warning("[增强] 找不到 study_document，跳过文档增强")
        return False
    if getattr(original, "_xxt_enhanced", False):
        logger.trace("[增强] study_document 已增强过")
        return True

    def enhanced_study_document(self, _course, _job):
        # 先走上游原逻辑（上报基础完成状态）
        result = original(self, _course, _job)

        if not ENABLE_DOCUMENT_TURN:
            return result

        try:
            session = _get_session()
            course_id = _course.get("courseId", "")
            clazz_id = _course.get("clazzId", "")
            kid = _job_knowledge_id(_job)
            objectid = _job.get("objectid", "")

            if not kid:
                logger.debug("[增强] 文档任务缺 knowledgeId，跳过翻阅")
                return result

            # 打开知识点页面 —— 这一步让平台记录"用户确实看了"
            page_url = (
                "https://mooc1.chaoxing.com/mooc-ans/knowledge/cards"
                f"?clazzid={clazz_id}&courseid={course_id}"
                f"&knowledgeid={kid}&num=0&ut=s"
            )
            resp = session.get(page_url, timeout=20)
            logger.debug(f"[增强] 文档页打开 status={resp.status_code} len={len(resp.text)}")

            # 部分文档需要单独上报一次浏览事件
            if objectid:
                try:
                    view_url = (
                        "https://mooc1.chaoxing.com/ananas/job/document"
                        f"?jobid={_job.get('jobid', '')}"
                        f"&knowledgeid={kid}"
                        f"&courseid={course_id}"
                        f"&clazzid={clazz_id}"
                        f"&jtoken={_job.get('jtoken', '')}"
                        f"&_dc={int(time.time() * 1000)}"
                    )
                    session.get(view_url, timeout=20)
                except Exception:
                    pass

            # 停留一会儿，模拟真人阅读
            time.sleep(2)
            logger.info(f"[增强] 文档任务已翻阅：{kid}")

        except Exception as exc:
            logger.debug(f"[增强] 文档翻阅出错（不影响主流程）：{exc}")

        return result

    enhanced_study_document._xxt_enhanced = True  # type: ignore[attr-defined]
    Chaoxing.study_document = enhanced_study_document

    # api.base 内部可能持有引用，同步替换
    try:
        import api.base as B
        B.Chaoxing.study_document = enhanced_study_document
    except Exception:
        pass

    logger.info("[增强] 文档任务：已启用自动翻阅")
    return True


# ---------------------------------------------------------------- 2. 链接任务

def _process_link_task(card: dict) -> Optional[dict]:
    """把 type=link 的附件转成内部任务结构。

    上游 decode.py 不认识 link，会走到 "未知任务类型" 返回 ERROR。
    这里给它一个能被 process_job 识别的类型。
    """
    try:
        prop = card.get("property") or {}
        return {
            "type": "link",
            "jobid": card.get("jobid", "") or card.get("job", ""),
            "name": prop.get("name", "") or card.get("name", "链接任务"),
            "otherinfo": card.get("otherInfo", "") or card.get("otherinfo", ""),
            "mid": card.get("mid", ""),
            "aid": card.get("aid", ""),
            "enc": card.get("enc", ""),
            "objectid": card.get("objectId", "") or card.get("objectid", ""),
            "url": prop.get("url", "") or card.get("url", ""),
        }
    except Exception:
        return None


def _patch_link_task() -> bool:
    """让 decode 认识 link 类型，并让 process_job 能处理它。"""
    try:
        import api.decode as D
        import api.base as B
        from api.base import StudyResult
    except Exception as exc:
        logger.warning(f"[增强] 导入失败：{exc}")
        return False

    # ---- (a) decode 层：把 link 加进类型分派
    orig_proc = getattr(D, "_process_attachment_cards", None)
    if orig_proc and not getattr(orig_proc, "_xxt_link_enhanced", False):

        def enhanced_process_attachment_cards(cards):
            job_list = []
            for card in cards or []:
                if card.get("isPassed", False):
                    continue
                ctype = str(card.get("type", "")).lower()
                if ctype in ("link", "url", "hyperlink"):
                    link_job = _process_link_task(card)
                    if link_job:
                        job_list.append(link_job)
                        continue
                # 其它类型交回原逻辑单条处理
                sub = orig_proc([card])
                job_list.extend(sub)
            return job_list

        enhanced_process_attachment_cards._xxt_link_enhanced = True  # type: ignore[attr-defined]
        D._process_attachment_cards = enhanced_process_attachment_cards
        B._process_attachment_cards = enhanced_process_attachment_cards
        logger.info("[增强] 链接任务：decode 层已支持 link 类型")

    # ---- (b) base 层：加 study_link 方法
    if not hasattr(B.Chaoxing, "study_link") or not getattr(
            B.Chaoxing.study_link, "_xxt_enhanced", False):

        def study_link(self, _course, _job) -> "StudyResult":
            """访问链接任务的目标地址，让平台记为已完成。"""
            try:
                session = _get_session()
                kid = _job_knowledge_id(_job)

                # 首选：走超星自己的「完成任务」接口
                if kid:
                    done_url = (
                        "https://mooc1.chaoxing.com/ananas/job/document"
                        f"?jobid={_job.get('jobid', '')}"
                        f"&knowledgeid={kid}"
                        f"&courseid={_course.get('courseId', '')}"
                        f"&clazzid={_course.get('clazzId', '')}"
                        f"&jtoken={_job.get('jtoken', _job.get('enc', ''))}"
                        f"&_dc={int(time.time() * 1000)}"
                    )
                    r = session.get(done_url, timeout=20)
                    logger.debug(f"[增强] 链接任务上报 status={r.status_code}")

                # 再访问一次真实目标（部分任务要求真的打开）
                target = _job.get("url") or ""
                if target.startswith("http"):
                    try:
                        session.get(target, timeout=15)
                        logger.debug(f"[增强] 已访问目标链接 {target[:60]}")
                    except Exception:
                        pass

                time.sleep(1)
                logger.info(f"[增强] 链接任务已处理：{_job.get('name', kid)}")
                return StudyResult.SUCCESS
            except Exception as exc:
                logger.warning(f"[增强] 链接任务失败：{exc}")
                return StudyResult.ERROR

        study_link._xxt_enhanced = True  # type: ignore[attr-defined]
        B.Chaoxing.study_link = study_link

    # ---- (c) main 层：让 process_job 认识 link
    try:
        import main as M
        orig_pj = M.process_job

        if not getattr(orig_pj, "_xxt_link_enhanced", False):

            def enhanced_process_job(chaoxing, course, job, job_info, speed):
                if job.get("type") == "link":
                    logger.trace(f"[增强] 识别到链接任务: {job.get('name', '')}")
                    return chaoxing.study_link(course, job)
                return orig_pj(chaoxing, course, job, job_info, speed)

            enhanced_process_job._xxt_link_enhanced = True  # type: ignore[attr-defined]
            M.process_job = enhanced_process_job
            logger.info("[增强] 链接任务：process_job 已支持 link")
    except Exception as exc:
        logger.debug(f"[增强] 打补丁到 main 失败：{exc}")

    return True


# ---------------------------------------------------------------- 3. 人脸识别

def _patch_face_notify() -> bool:
    """视频播到一半弹人脸时，推通知。"""
    if not ENABLE_FACE_NOTIFY:
        return False

    try:
        import api.base as B
    except Exception as exc:
        logger.warning(f"[增强] 导入 base 失败：{exc}")
        return False

    # 包一层 video_progress_log：它每次都解析服务器响应，
    # 响应里出现人脸关键字就立刻通知（只需通知一次，用 flag 防重复）
    orig = getattr(B.Chaoxing, "video_progress_log", None)
    if orig is None:
        logger.debug("[增强] 找不到 video_progress_log，跳过人脸通知")
        return False
    if getattr(orig, "_xxt_face_enhanced", False):
        return True

    state = {"notified": False}

    def enhanced_video_progress_log(self, *args, **kwargs):
        # 重置通知标志（每个视频重新计）
        result = orig(self, *args, **kwargs)

        if state["notified"]:
            return result

        try:
            # 尝试从返回值/响应里找人脸特征
            text = ""
            if isinstance(result, dict):
                text = json.dumps(result, ensure_ascii=False)
            elif isinstance(result, str):
                text = result
            elif hasattr(result, "text"):
                text = result.text

            if _has_face_check(text):
                state["notified"] = True
                logger.warning("=" * 60)
                logger.warning("⚠ 检测到人脸识别校验！请立刻去学习通处理")
                logger.warning("=" * 60)
                _send_face_notification()
        except Exception:
            pass

        return result

    enhanced_video_progress_log._xxt_face_enhanced = True  # type: ignore[attr-defined]
    B.Chaoxing.video_progress_log = enhanced_video_progress_log

    logger.info("[增强] 人脸识别：已启用检测与通知")
    return True


def _send_face_notification() -> None:
    """发一条人脸校验通知，复用程序里已配的通知渠道。"""
    try:
        from api.notification import Notification
        notify = Notification()
        notify.send(
            title="⚠ 学习通需要人脸识别",
            content="程序检测到人脸校验，请立刻打开学习通 APP 处理，否则进度会中断。",
        )
        logger.info("[增强] 人脸通知已发送")
    except Exception as exc:
        # 没配通知渠道是正常的，只记 debug
        logger.debug(f"[增强] 人脸通知发送失败（可能没配推送）：{exc}")


# ---------------------------------------------------------------- 4. 验证码通知

def _patch_captcha_notify() -> bool:
    """遇到验证码通关失败时推通知。

    为什么单独做：
        2026-11-08 的真实日志里，验证码连续 11 次通关失败、跳过了 11 个任务点，
        但整个过程**没有任何提示**，用户只看到程序"跑完了"，
        实际大部分任务都没刷上 —— 这是最坑的一种失败。

    切入点选择：
        内核里处理验证码的是 api/captcha.py 的 CxCaptcha.try_pass()。
        它被 video_progress_log 内部的一个局部函数调用，没有类的挂载点，
        所以直接包 CxCaptcha.try_pass —— 这是所有验证码尝试的必经之路。
    """
    if not ENABLE_CAPTCHA_NOTIFY:
        return False

    try:
        import api.captcha as cap
    except Exception as exc:
        logger.warning(f"[增强] 导入 captcha 失败：{exc}")
        return False

    orig = getattr(cap.CxCaptcha, "try_pass", None)
    if orig is None:
        logger.debug("[增强] 找不到 CxCaptcha.try_pass，跳过验证码通知")
        return False
    if getattr(orig, "_xxt_captcha_notify", False):
        return True

    # 一次运行只推一次，避免刷屏
    state = {"notified": False}

    def enhanced_try_pass(self, *args, **kwargs):
        try:
            ok = orig(self, *args, **kwargs)
        except Exception:
            ok = False

        if not ok and not state["notified"]:
            state["notified"] = True
            logger.warning("=" * 62)
            logger.warning("⚠ 验证码自动识别失败 —— 后面的任务点会被跳过")
            logger.warning("   请立刻去浏览器打开 i.chaoxing.com 手动点掉验证码")
            logger.warning("   然后把「同时进行的章节数」改成 1、「重试间隔」改成 5 秒")
            logger.warning("=" * 62)
            _send_captcha_notification()

        return ok

    enhanced_try_pass._xxt_captcha_notify = True  # type: ignore[attr-defined]
    cap.CxCaptcha.try_pass = enhanced_try_pass

    logger.info("[增强] 验证码通知：已启用")
    return True


def _send_captcha_notification() -> None:
    """发一条验证码通知，复用程序里已配的通知渠道。"""
    try:
        from api.notification import Notification
        notify = Notification()
        notify.send(
            title="⚠ 学习通验证码拦截",
            content=(
                "程序连续遇到验证码且无法自动通过，部分任务点已被跳过。\n\n"
                "请立刻：\n"
                "1. 浏览器打开 i.chaoxing.com，手动把验证码点掉\n"
                "2. 把「同时进行的章节数」改成 1\n"
                "3. 把「失败重试间隔」改成 5 秒\n"
                "4. 再重新开始"
            ),
        )
        logger.info("[增强] 验证码通知已发送")
    except Exception as exc:
        logger.debug(f"[增强] 验证码通知发送失败（可能没配推送）：{exc}")


# ---------------------------------------------------------------- 入口

def apply_all() -> list[str]:
    """应用全部增强，返回生效的项名列表。"""
    _load_flags()   # 先读界面上的勾选状态

    enabled: list[str] = []
    for name, fn, flag in (
        ("文档自动翻阅", _patch_document, ENABLE_DOCUMENT_TURN),
        ("链接任务", _patch_link_task, ENABLE_LINK_TASK),
        ("人脸识别通知", _patch_face_notify, ENABLE_FACE_NOTIFY),
        ("验证码通知", _patch_captcha_notify, ENABLE_CAPTCHA_NOTIFY),
    ):
        if not flag:
            logger.debug(f"[增强] {name}：界面里未勾选，跳过")
            continue
        try:
            if fn():
                enabled.append(name)
        except Exception as exc:
            logger.warning(f"[增强] {name} 应用失败：{exc}")
    if enabled:
        logger.info(f"[增强] 共启用 {len(enabled)} 项：{'、'.join(enabled)}")
    else:
        logger.info("[增强] 全部关闭（界面里没勾选）")
    return enabled
