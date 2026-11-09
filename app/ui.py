# -*- coding: utf-8 -*-
"""学习通刷课助手 —— 图形界面（Tkinter，零第三方 UI 依赖）。

界面分三块：
  左：账号 / 课程 / 参数
  右上：运行日志
  右下：题库与通知（折叠式）
"""

from __future__ import annotations

import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

import iniio
from runner import Runner

APP_TITLE = "学习通刷课助手"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE = os.path.join(ROOT, "core")
# config.ini 必须放在 core\ 下：上游 Tiku 用 os.getcwd() 找默认配置文件，
# 而我们将 cwd 设为 core。放外层会导致题库配置读不到。
CONFIG = os.path.join(CORE, "config.ini")
TEMPLATE = os.path.join(CORE, "config_template.ini")
COOKIE = os.path.join(CORE, "cookies.txt")
# 上游 logger 把 chaoxing.log 写在 cwd（= core），不是项目根
LOG_FILE = os.path.join(CORE, "chaoxing.log")
PYTHON = os.path.join(ROOT, "_runtime", "python.exe")
PYTHONW = os.path.join(ROOT, "_runtime", "pythonw.exe")

PROVIDERS = [
    ("TikuGo", "GO题（免费，公众号申请 token 可选）"),
    ("TikuYanxi", "言溪题库（付费 token）"),
    ("TikuLike", "LIKE 知识库（付费 token）"),
    ("TikuAdapter", "TikuAdapter（自建开源服务）"),
    ("AI", "AI 大模型（OpenAI 兼容接口）"),
    ("SiliconFlow", "硅基流动（国内直连）"),
    ("TikuManual", "手动输入（运行到题目时自己填）"),
]

NOTIFIERS = [
    ("", "不推送"),
    ("ServerChan", "Server酱"),
    ("Qmsg", "Qmsg酱"),
    ("Bark", "Bark（iOS）"),
    ("Telegram", "Telegram Bot"),
]

NOTOPEN = [
    ("retry", "重试（默认，推荐）"),
    ("continue", "自动跳过已关闭章节"),
    ("ask", "每次都询问我"),
]


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self._setup_geometry()

        self.cfg: dict[str, dict[str, str]] = {}
        self.runner: Runner | None = None
        self.log_queue: queue.Queue = queue.Queue()
        self.running = False
        self.start_ts = 0.0
        self.done_chapters = 0
        self._alive = True
        self._tb_count = 0
        self.var_paused = tk.BooleanVar(value=False)
        # 展开 ④ 前的窗口几何 (w, h)，收起时还原；None 表示当前没展开过
        self._geo_before_adv: tuple[int, int] | None = None
        self._build_style()
        self._build_ui()
        self._load_config()
        self.after(120, self._drain_log)
        self.after(500, self._tick)

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------- 样式 ----------
    def _setup_geometry(self) -> None:
        """按屏幕实际大小决定窗口尺寸。

        为什么要这么做（2026-11-08 实际反馈）：
            原来写死 geometry("1180x760") + minsize(1020, 680)。
            笔记本常见分辨率 1366×768，扣掉任务栏可用高度只有 ~728px，
            760 高的窗口一启动就超出屏幕底部。
            展开「④ 章节检测答题」时更糟：面板要 390px，
            窗口被 screenheight-80 卡住没法再长，只能去挤日志区，
            结果日志被压没，用户以为"运行日志被遮完了"。

        策略：
            1. 目标尺寸取「设计尺寸」和「屏幕可用区域」的较小值
            2. minsize 也跟着放宽，小屏笔记本也要能开
            3. 居中显示，别让窗口跑到屏幕外
        """
        # 设计尺寸（宽屏下最舒服的大小）
        want_w, want_h = 1180, 760

        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()

        # 预留任务栏 + 标题栏空间：宽留 60，高留 90
        avail_w = max(900, sw - 60)
        avail_h = max(600, sh - 90)

        win_w = min(want_w, avail_w)
        win_h = min(want_h, avail_h)

        # 小屏时把最小尺寸也降下来，否则窗口根本无法小于 minsize
        min_w = min(1020, win_w)
        min_h = min(680, win_h)

        # 居中（Tk 的 geometry 支持 +x+y 相对屏幕左上角）
        x = max(0, (sw - win_w) // 2)
        y = max(0, (sh - win_h) // 2 - 20)   # 稍微上移一点，避开任务栏

        self.geometry(f"{win_w}x{win_h}+{x}+{y}")
        self.minsize(min_w, min_h)

        # 记下来，展开面板时用
        self._design_w = win_w
        self._avail_h = avail_h

    def _build_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 15, "bold"))
        style.configure("Sub.TLabel", font=("Microsoft YaHei UI", 9), foreground="#666")
        style.configure("Card.TLabelframe", padding=10)
        style.configure("Card.TLabelframe.Label", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Go.TButton", font=("Microsoft YaHei UI", 11, "bold"), padding=(18, 8))
        style.configure("Small.TButton", padding=(8, 4))
        style.configure("Stat.TLabel", font=("Microsoft YaHei UI", 10))

    # ---------- 布局 ----------
    def _build_ui(self) -> None:
        header = ttk.Frame(self, padding=(14, 10, 14, 4))
        header.pack(fill="x")
        ttk.Label(header, text=APP_TITLE, style="Title.TLabel").pack(side="left")
        ttk.Label(
            header,
            text="内核 Samueli924/chaoxing (GPL-3.0)  ·  本机独立运行，不上传任何数据",
            style="Sub.TLabel",
        ).pack(side="left", padx=(12, 0), pady=(6, 0))
        ttk.Button(header, text="说明", style="Small.TButton",
                   command=self._open_readme).pack(side="right")
        ttk.Button(header, text="打开数据目录", style="Small.TButton",
                   command=self._open_folder).pack(side="right", padx=(0, 6))

        body = ttk.Frame(self, padding=(14, 4, 14, 10))
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=0, minsize=430)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        left = ttk.Frame(body)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        left.rowconfigure(2, weight=1)

        self._build_account(left)
        self._build_course(left)
        self._build_params(left)

        right = ttk.Frame(body)
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)

        self._build_status(right)
        self._build_log(right)
        self._build_advanced(right)

    def _build_account(self, parent: ttk.Frame) -> None:
        box = ttk.LabelFrame(parent, text="① 账号", style="Card.TLabelframe")
        box.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        box.columnconfigure(1, weight=1)

        self.var_cookie_login = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            box, text="用 Cookie 登录（免密码，适合有验证码的账号）",
            variable=self.var_cookie_login, command=self._toggle_cookie,
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))

        ttk.Label(box, text="手机号").grid(row=1, column=0, sticky="w")
        self.ent_user = ttk.Entry(box)
        self.ent_user.grid(row=1, column=1, columnspan=2, sticky="ew", padx=(8, 0))

        ttk.Label(box, text="密码").grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.ent_pass = ttk.Entry(box, show="*")
        self.ent_pass.grid(row=2, column=1, sticky="ew", padx=(8, 0), pady=(6, 0))
        self.var_show = tk.BooleanVar(value=False)
        ttk.Checkbutton(box, text="显示", variable=self.var_show,
                        command=self._toggle_pwd).grid(row=2, column=2, padx=(6, 0), pady=(6, 0))

        self.lbl_cookie = ttk.Label(box, text="", style="Sub.TLabel", wraplength=390)
        self.lbl_cookie.grid(row=3, column=0, columnspan=3, sticky="w", pady=(6, 0))

        cookierow = ttk.Frame(box)
        cookierow.grid(row=4, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Button(cookierow, text="粘贴 Cookie", style="Small.TButton",
                   command=self._paste_cookie).pack(side="left")
        ttk.Button(cookierow, text="清除 Cookie", style="Small.TButton",
                   command=self._clear_cookie).pack(side="left", padx=(6, 0))
        self.lbl_cookie_state = ttk.Label(cookierow, text="", style="Sub.TLabel")
        self.lbl_cookie_state.pack(side="left", padx=(10, 0))

    def _build_course(self, parent: ttk.Frame) -> None:
        box = ttk.LabelFrame(parent, text="② 课程", style="Card.TLabelframe")
        box.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        box.columnconfigure(0, weight=1)

        ttk.Label(
            box,
            text="留空 = 运行时把课程列表打给你，你输入编号或 ID；也可直接填课程 ID，逗号分隔。",
            style="Sub.TLabel", wraplength=395,
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))

        self.ent_courses = ttk.Entry(box)
        self.ent_courses.grid(row=1, column=0, sticky="ew")

        row = ttk.Frame(box)
        row.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        ttk.Button(row, text="识别我的课程", style="Small.TButton",
                   command=self._list_courses).pack(side="left")
        ttk.Label(row, text="（识别结果会写进上方输入框）", style="Sub.TLabel").pack(side="left", padx=(8, 0))

    def _build_params(self, parent: ttk.Frame) -> None:
        box = ttk.LabelFrame(parent, text="③ 参数", style="Card.TLabelframe")
        box.grid(row=2, column=0, sticky="nsew")
        box.columnconfigure(1, weight=1)

        ttk.Label(box, text="同时进行的章节数").grid(row=0, column=0, sticky="w")
        self.var_jobs = tk.IntVar(value=2)
        ttk.Spinbox(box, from_=1, to=12, textvariable=self.var_jobs, width=6
                    ).grid(row=0, column=1, sticky="w", padx=(8, 0))
        ttk.Label(box, text="越高越快，但更容易被风控；4 最稳", style="Sub.TLabel"
                  ).grid(row=0, column=2, sticky="w", padx=(8, 0))

        ttk.Label(box, text="任务点关闭时").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.var_notopen = tk.StringVar()
        cb = ttk.Combobox(box, textvariable=self.var_notopen, state="readonly", width=26,
                          values=[f"{k} · {v}" for k, v in NOTOPEN])
        cb.grid(row=1, column=1, columnspan=2, sticky="w", padx=(8, 0), pady=(8, 0))

        ttk.Label(box, text="失败重试间隔(秒)").grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.var_retry = tk.DoubleVar(value=3.0)
        ttk.Spinbox(box, from_=0.5, to=30.0, increment=0.5, textvariable=self.var_retry, width=6
                    ).grid(row=2, column=1, sticky="w", padx=(8, 0), pady=(8, 0))

        self.var_learn_count = tk.BooleanVar(value=False)
        ttk.Checkbutton(box, text="刷完后顺带刷章节学习次数",
                        variable=self.var_learn_count).grid(
            row=3, column=0, columnspan=3, sticky="w", pady=(10, 0))
        rowc = ttk.Frame(box)
        rowc.grid(row=4, column=0, columnspan=3, sticky="w", pady=(4, 0))
        ttk.Label(rowc, text="目标次数", style="Sub.TLabel").pack(side="left")
        self.var_target = tk.IntVar(value=100)
        ttk.Spinbox(rowc, from_=10, to=2000, increment=10, textvariable=self.var_target, width=7
                    ).pack(side="left", padx=(6, 0))

        ttk.Separator(box, orient="horizontal").grid(
            row=5, column=0, columnspan=3, sticky="ew", pady=(12, 10))

        btnrow = ttk.Frame(box)
        btnrow.grid(row=6, column=0, columnspan=3, sticky="ew")
        self.btn_start = ttk.Button(btnrow, text="▶  开始刷课", style="Go.TButton",
                                    command=self._start)
        self.btn_start.pack(side="left")
        self.btn_pause = ttk.Button(btnrow, text="⏸  暂停", style="Small.TButton",
                                    command=self._toggle_pause, state="disabled")
        self.btn_pause.pack(side="left", padx=(8, 0))
        self.btn_stop = ttk.Button(btnrow, text="■  停止", style="Small.TButton",
                                   command=self._stop, state="disabled")
        self.btn_stop.pack(side="left", padx=(8, 0))
        # 强制退出：内核可能卡在某个网络请求里，正常停止要等它自己退出。
        # 这个按钮不管三七二十一，直接杀进程 + 关窗口，是最后手段。
        ttk.Button(btnrow, text="✕  强制退出", style="Small.TButton",
                   command=self._force_quit).pack(side="right")

    def _build_status(self, parent: ttk.Frame) -> None:
        box = ttk.LabelFrame(parent, text="运行状态", style="Card.TLabelframe")
        box.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        box.columnconfigure(3, weight=1)

        self.var_state = tk.StringVar(value="空闲")
        self.var_timer = tk.StringVar(value="00:00")
        self.var_chapter = tk.StringVar(value="—")
        self.var_course = tk.StringVar(value="—")

        ttk.Label(box, text="状态", style="Sub.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(box, textvariable=self.var_state, style="Stat.TLabel"
                  ).grid(row=0, column=1, sticky="w", padx=(8, 22))
        ttk.Label(box, text="已运行", style="Sub.TLabel").grid(row=0, column=2, sticky="w")
        ttk.Label(box, textvariable=self.var_timer, style="Stat.TLabel"
                  ).grid(row=0, column=3, sticky="w", padx=(8, 0))

        ttk.Label(box, text="当前课程", style="Sub.TLabel").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Label(box, textvariable=self.var_course, style="Stat.TLabel"
                  ).grid(row=1, column=1, columnspan=3, sticky="w", padx=(8, 0), pady=(6, 0))

        ttk.Label(box, text="当前章节", style="Sub.TLabel").grid(row=2, column=0, sticky="w", pady=(4, 0))
        ttk.Label(box, textvariable=self.var_chapter, style="Stat.TLabel"
                  ).grid(row=2, column=1, columnspan=3, sticky="w", padx=(8, 0), pady=(4, 0))

        # 第 4 行：已完成章节数 / 进度 / 生效补丁
        self.var_done = tk.StringVar(value="0 章")
        self.var_progress = tk.StringVar(value="—")
        self.var_patch = tk.StringVar(value="—")

        ttk.Label(box, text="已完成", style="Sub.TLabel").grid(row=3, column=0, sticky="w", pady=(4, 0))
        ttk.Label(box, textvariable=self.var_done, style="Stat.TLabel"
                  ).grid(row=3, column=1, sticky="w", padx=(8, 0), pady=(4, 0))
        ttk.Label(box, text="进度", style="Sub.TLabel").grid(row=3, column=2, sticky="w", pady=(4, 0))
        ttk.Label(box, textvariable=self.var_progress, style="Stat.TLabel"
                  ).grid(row=3, column=3, sticky="w", padx=(8, 0), pady=(4, 0))

        ttk.Label(box, text="已启用补丁", style="Sub.TLabel").grid(row=4, column=0, sticky="w", pady=(4, 0))
        ttk.Label(box, textvariable=self.var_patch, style="Sub.TLabel", foreground="#2e7d32"
                  ).grid(row=4, column=1, columnspan=3, sticky="w", padx=(8, 0), pady=(4, 0))

    def _build_log(self, parent: ttk.Frame) -> None:
        box = ttk.LabelFrame(parent, text="运行日志", style="Card.TLabelframe")
        box.grid(row=1, column=0, sticky="nsew")
        box.rowconfigure(0, weight=1)
        box.columnconfigure(0, weight=1)

        self.txt = tk.Text(box, wrap="none", font=("Consolas", 9), bg="#1e1e1e", fg="#d4d4d4",
                           insertbackground="#d4d4d4", relief="flat", padx=8, pady=6,
                           height=10)
        self.txt.grid(row=0, column=0, sticky="nsew")
        # 最小高度：展开 ④ 面板时日志区允许被压，但不能压到看不清
        self.txt.configure(height=10)

        ysb = ttk.Scrollbar(box, orient="vertical", command=self.txt.yview)
        ysb.grid(row=0, column=1, sticky="ns")
        xsb = ttk.Scrollbar(box, orient="horizontal", command=self.txt.xview)
        xsb.grid(row=1, column=0, sticky="ew")
        self.txt.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)

        self.txt.tag_configure("err", foreground="#f48771")
        self.txt.tag_configure("warn", foreground="#dcdcaa")
        self.txt.tag_configure("ok", foreground="#6a9955")
        self.txt.tag_configure("dim", foreground="#808080")

        bar = ttk.Frame(box)
        bar.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        ttk.Button(bar, text="清空", style="Small.TButton", command=self._clear_log).pack(side="right")
        self.var_auto = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="自动滚动", variable=self.var_auto).pack(side="right", padx=(0, 10))
        ttk.Button(bar, text="打开完整日志", style="Small.TButton",
                   command=self._open_logfile).pack(side="left")
        ttk.Button(bar, text="导出诊断包", style="Small.TButton",
                   command=self._export_diag).pack(side="left", padx=(6, 0))

    def _build_advanced(self, parent: ttk.Frame) -> None:
        outer = ttk.Frame(parent)
        outer.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        outer.columnconfigure(0, weight=1)

        self.adv_open = tk.BooleanVar(value=False)
        head = ttk.Frame(outer)
        head.grid(row=0, column=0, sticky="ew")
        ttk.Checkbutton(head, text="④ 章节检测答题 / 通知推送（不配也能刷视频，但章节检测会被跳过）",
                        variable=self.adv_open, command=self._toggle_adv).pack(side="left")

        self.adv = ttk.LabelFrame(outer, text="答题与推送", style="Card.TLabelframe")
        self.adv.columnconfigure(1, weight=1)
        self.adv.columnconfigure(3, weight=1)

        ttk.Label(self.adv, text="答题来源").grid(row=0, column=0, sticky="w")
        self.var_provider = tk.StringVar()
        self.cb_provider = ttk.Combobox(
            self.adv, textvariable=self.var_provider, state="readonly", width=34,
            values=[f"{k} · {v}" for k, v in PROVIDERS])
        self.cb_provider.grid(row=0, column=1, columnspan=3, sticky="w", padx=(8, 0))
        self.cb_provider.bind("<<ComboboxSelected>>", lambda _e: self._sync_provider())

        self.lbl_tip = ttk.Label(self.adv, text="", style="Sub.TLabel", wraplength=640)
        self.lbl_tip.grid(row=1, column=0, columnspan=4, sticky="w", pady=(6, 0))

        ttk.Label(self.adv, text="Token / Key").grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.ent_token = ttk.Entry(self.adv)
        self.ent_token.grid(row=2, column=1, columnspan=3, sticky="ew", padx=(8, 0), pady=(8, 0))

        ttk.Label(self.adv, text="接口地址").grid(row=3, column=0, sticky="w", pady=(6, 0))
        self.ent_endpoint = ttk.Entry(self.adv)
        self.ent_endpoint.grid(row=3, column=1, columnspan=3, sticky="ew", padx=(8, 0), pady=(6, 0))

        ttk.Label(self.adv, text="模型名").grid(row=4, column=0, sticky="w", pady=(6, 0))
        self.ent_model = ttk.Entry(self.adv)
        self.ent_model.grid(row=4, column=1, sticky="ew", padx=(8, 0), pady=(6, 0))
        ttk.Label(self.adv, text="（AI/SiliconFlow 才需要）", style="Sub.TLabel"
                  ).grid(row=4, column=2, columnspan=2, sticky="w", padx=(8, 0), pady=(6, 0))

        ttk.Label(self.adv, text="题库自建地址").grid(row=5, column=0, sticky="w", pady=(6, 0))
        self.ent_tiku_url = ttk.Entry(self.adv)
        self.ent_tiku_url.grid(row=5, column=1, columnspan=3, sticky="ew", padx=(8, 0), pady=(6, 0))

        self.var_submit = tk.BooleanVar(value=True)
        ttk.Checkbutton(self.adv, text="达到覆盖率就自动提交（不勾则只保存，你自己去学习通提交）",
                        variable=self.var_submit).grid(
            row=6, column=0, columnspan=4, sticky="w", pady=(10, 0))

        rowcr = ttk.Frame(self.adv)
        rowcr.grid(row=7, column=0, columnspan=4, sticky="w", pady=(6, 0))
        ttk.Label(rowcr, text="最低覆盖率(%)").pack(side="left")
        self.var_cover = tk.IntVar(value=90)
        ttk.Spinbox(rowcr, from_=0, to=100, increment=5, textvariable=self.var_cover, width=6
                    ).pack(side="left", padx=(6, 18))
        ttk.Label(rowcr, text="查题间隔(秒)").pack(side="left")
        self.var_delay = tk.DoubleVar(value=1.0)
        ttk.Spinbox(rowcr, from_=0.0, to=10.0, increment=0.5, textvariable=self.var_delay, width=6
                    ).pack(side="left", padx=(6, 0))

        ttk.Separator(self.adv, orient="horizontal").grid(
            row=8, column=0, columnspan=4, sticky="ew", pady=(12, 10))

        # ---- 任务类型增强（对应 app/enhance.py 里的四个开关）
        ttk.Label(self.adv, text="任务类型增强").grid(row=9, column=0, sticky="w")
        enh = ttk.Frame(self.adv)
        enh.grid(row=9, column=1, columnspan=3, sticky="w", padx=(8, 0))

        self.var_enh_doc = tk.BooleanVar(value=True)
        ttk.Checkbutton(enh, text="文档/PPT 自动翻阅",
                        variable=self.var_enh_doc).pack(side="left")
        self.var_enh_link = tk.BooleanVar(value=True)
        ttk.Checkbutton(enh, text="链接任务自动完成",
                        variable=self.var_enh_link).pack(side="left", padx=(14, 0))
        self.var_enh_face = tk.BooleanVar(value=True)
        ttk.Checkbutton(enh, text="弹人脸时通知我",
                        variable=self.var_enh_face).pack(side="left", padx=(14, 0))
        self.var_enh_captcha = tk.BooleanVar(value=True)
        ttk.Checkbutton(enh, text="验证码失败时通知我",
                        variable=self.var_enh_captcha).pack(side="left", padx=(14, 0))

        ttk.Label(
            self.adv,
            text="四项都是「不开就出问题」的类型：文档不翻阅不会标记完成；链接任务不点会卡住"
                 "后续章节；人脸/验证码不通知，程序会白跑一趟你还不知道。建议全开。",
            style="Sub.TLabel", wraplength=640,
        ).grid(row=10, column=0, columnspan=4, sticky="w", pady=(4, 0))

        # ---- 保守模式：一键把并发和间隔调到"不容易招风控"的值
        # 2026-11-08 实证：4 并发 + 1 秒间隔跑了 36 分钟，11 个视频只成功 2 个，
        # 其余全被 403 验证码拦掉。改成 1 并发 + 5 秒后成功率显著提升。
        cons = ttk.Frame(self.adv)
        cons.grid(row=11, column=0, columnspan=4, sticky="w", pady=(8, 0))
        ttk.Button(cons, text="⚙ 套用保守模式", style="Small.TButton",
                   command=self._apply_safe_mode).pack(side="left")
        ttk.Label(cons, text="遇到大量验证码/403 时点它：并发→1、重试间隔→5 秒（慢但稳）",
                  style="Sub.TLabel").pack(side="left", padx=(10, 0))

        ttk.Separator(self.adv, orient="horizontal").grid(
            row=12, column=0, columnspan=4, sticky="ew", pady=(12, 10))

        ttk.Label(self.adv, text="完成任务后推送").grid(row=13, column=0, sticky="w")
        self.var_notify = tk.StringVar()
        ttk.Combobox(self.adv, textvariable=self.var_notify, state="readonly", width=24,
                     values=[f"{k} · {v}" if k else v for k, v in NOTIFIERS]
                     ).grid(row=13, column=1, sticky="w", padx=(8, 0))
        self.ent_notify = ttk.Entry(self.adv)
        self.ent_notify.grid(row=13, column=2, columnspan=2, sticky="ew", padx=(8, 0))
        ttk.Label(self.adv, text="推送地址填这里（Server酱/Qmsg/Bark 的 URL，或 Telegram 的 chat_id）",
                  style="Sub.TLabel", wraplength=640).grid(
            row=14, column=0, columnspan=4, sticky="w", pady=(4, 0))

        savebar = ttk.Frame(self.adv)
        savebar.grid(row=15, column=0, columnspan=4, sticky="ew", pady=(12, 0))
        ttk.Button(savebar, text="保存设置", style="Small.TButton",
                   command=self._save_config).pack(side="left")
        ttk.Label(savebar, text="设置会自动保存；这里点一下可立即落盘", style="Sub.TLabel"
                  ).pack(side="left", padx=(10, 0))

        self._sync_provider()

    # ---------- 交互 ----------
    def _apply_safe_mode(self) -> None:
        """一键套用保守参数。

        背景（2026-11-08 实测）：
            默认 4 并发 + 1 秒重试，跑了 36 分钟，11 个视频只成功 2 个，
            其余全被学习通 403 验证码拦掉，账号还被加了风控标记。

            改成 1 并发 + 5 秒重试后，请求频率降到原来的 1/20，
            风控触发概率显著下降。代价是慢，但能刷上。

        这里只改两个最关键的参数，其它保持不动 —— 避免"一键"改太多让人找不回原值。
        """
        if messagebox.askyesno(
                APP_TITLE,
                "套用保守模式？\n\n"
                "同时进行的章节数：4 → 1\n"
                "失败重试间隔：1.0 → 5.0 秒\n\n"
                "适合：\n"
                "· 遇到大量验证码 / 403\n"
                "· 账号刚被风控过\n"
                "· 想放着跑过夜\n\n"
                "代价：速度约为原来的 1/4，但成功率高得多。"):
            self.var_jobs.set(1)
            self.var_retry.set(5.0)
            self._append(
                f"[{time.strftime('%H:%M:%S')}] 已套用保守模式："
                "并发 1、重试间隔 5 秒。建议先保存设置再开始。", "ok")

    def _toggle_pwd(self) -> None:
        self.ent_pass.configure(show="" if self.var_show.get() else "*")

    def _toggle_cookie(self) -> None:
        on = self.var_cookie_login.get()
        state = "disabled" if on else "normal"
        self.ent_user.configure(state=state)
        self.ent_pass.configure(state=state)
        if on:
            exists = os.path.exists(COOKIE)
            self.lbl_cookie.configure(
                text=("已检测到 core\\cookies.txt，可以直接跑。"
                      if exists else
                      "还没有 cookies.txt：点下面「粘贴 Cookie」，从浏览器复制后粘进来。"))
        else:
            self.lbl_cookie.configure(text="")
        self._refresh_cookie_state()

    def _refresh_cookie_state(self) -> None:
        if os.path.exists(COOKIE):
            size = os.path.getsize(COOKIE)
            self.lbl_cookie_state.configure(text=f"✓ 已保存（{size} 字节）", foreground="#2e7d32")
        else:
            self.lbl_cookie_state.configure(text="未设置", foreground="#999")

    def _paste_cookie(self) -> None:
        dlg = tk.Toplevel(self)
        dlg.title("粘贴 Cookie")
        dlg.geometry("720x400")
        dlg.transient(self)
        dlg.grab_set()

        ttk.Label(
            dlg,
            text=("1. 浏览器打开 i.chaoxing.com 并登录\n"
                  "2. 按 F12 → 网络(Network) → 点任意一个请求\n"
                  "3. 在「请求标头」里找到 Cookie，复制冒号后面的整段值\n"
                  "4. 粘到下面（一行，不要换行）"),
            justify="left",
        ).pack(anchor="w", padx=12, pady=(12, 6))

        txt = tk.Text(dlg, wrap="char", height=10, font=("Consolas", 9))
        txt.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        if os.path.exists(COOKIE):
            try:
                with open(COOKIE, "r", encoding="utf-8", errors="replace") as f:
                    txt.insert("1.0", f.read().strip())
            except OSError:
                pass

        def apply() -> None:
            raw = txt.get("1.0", "end").strip()
            raw = raw.replace("\r", "").replace("\n", " ")
            if not raw:
                messagebox.showwarning(APP_TITLE, "内容是空的。", parent=dlg)
                return
            if "=" not in raw:
                if not messagebox.askyesno(
                        APP_TITLE, "这段内容里没有「=」，看起来不像 Cookie。仍然保存吗？",
                        parent=dlg):
                    return
            try:
                os.makedirs(os.path.dirname(COOKIE), exist_ok=True)
                with open(COOKIE, "w", encoding="utf-8", newline="") as f:
                    f.write(raw)
            except OSError as exc:
                messagebox.showerror(APP_TITLE, f"写入失败：{exc}", parent=dlg)
                return
            self.var_cookie_login.set(True)
            self._toggle_cookie()
            self._append(f"[{time.strftime('%H:%M:%S')}] Cookie 已保存到 core\\cookies.txt", "ok")
            dlg.destroy()

        bar = ttk.Frame(dlg)
        bar.pack(fill="x", padx=12, pady=(0, 12))
        ttk.Button(bar, text="保存并使用", command=apply).pack(side="right")
        ttk.Button(bar, text="取消", command=dlg.destroy).pack(side="right", padx=(0, 8))

    def _clear_cookie(self) -> None:
        if not os.path.exists(COOKIE):
            return
        if not messagebox.askyesno(APP_TITLE, "确定删除已保存的 Cookie？"):
            return
        try:
            os.remove(COOKIE)
        except OSError as exc:
            messagebox.showerror(APP_TITLE, f"删除失败：{exc}")
            return
        self._refresh_cookie_state()
        self._append("Cookie 已清除。")

    def _try_maximize(self) -> None:
        """把窗口最大化。

        仅当屏幕小到"面板 + 最小日志区"都放不下时调用。
        用 Windows 的 maximize 属性而不是改 geometry ——
        后者要自己算任务栏高度，容易算错（高 DPI 下更麻烦）。
        """
        try:
            # state('zoomed') 在 Windows 上等价于最大化
            self.state("zoomed")
        except Exception:
            # 某些 Linux 窗口管理器不支持 zoomed，退回铺满屏幕
            try:
                sw = self.winfo_screenwidth()
                sh = self.winfo_screenheight()
                self.geometry(f"{sw}x{sh}+0+0")
            except Exception:
                pass

    def _toggle_adv(self) -> None:
        """展开/收起「④ 章节检测答题 / 通知推送」。

        历次踩的坑，按时间顺序：
          1. 最早只做 grid/grid_remove —— 面板撑出窗口外，下半截看不见，
             必须取消勾选才能恢复。
          2. 改成"展开时把窗口加高" —— 在 1080p 桌面好用，
             但 1366×768 笔记本上窗口就没法再长（被屏幕高度卡住），
             加高请求变成"压缩到 screenheight-80"，日志区被挤到 0 高度，
             用户看到的就是"运行日志被遮完了"。

        现在的做法（小屏安全）：
          · 先试着加高窗口（有余量时最舒服）
          · 加高不够时，让**日志区**缩到最小（保留 6 行，不至于完全看不见）
          · 收起时两者都还原
        """
        if self.adv_open.get():
            self.adv.grid(row=1, column=0, sticky="ew", pady=(6, 0))
            self.update_idletasks()

            need = self.adv.winfo_reqheight()
            if need <= 1:
                need = 390  # 兜底

            cur_w = self.winfo_width()
            cur_h = self.winfo_height()

            if self._geo_before_adv is None:
                self._geo_before_adv = (cur_w, cur_h)

            # 屏幕还能给我们多少高度
            screen_h = self.winfo_screenheight()
            screen_w = self.winfo_screenwidth()
            # 小屏笔记本（1366×768）上，光留 80px 不够 —— 任务栏 + 标题栏
            # 实测量到 89px，留 100 更稳
            max_h = max(600, screen_h - 100)

            # 先看能不能靠加高窗口解决
            target_h = min(cur_h + need + 20, max_h)
            if target_h > cur_h:
                self.geometry(f"{cur_w}x{target_h}")
                self.update_idletasks()

            # 加高后窗口仍然装不下面板 → 让日志区让位
            try:
                win_h = self.winfo_height()
                # 估算：窗口高 - 状态栏 - 面板 - 其它padding
                other = 260   # 状态栏 + 标题栏 + 边距的实测经验值
                room = win_h - other - need
                lines = max(6, int(room / 16))   # 每行约 16px
                self.txt.configure(height=lines)

                # 兜底判定：面板 + 6 行日志都塞不下 → 最大化
                # room < 90 意味着日志区连 6 行（约 96px）都保不住
                if room < 100:
                    self._try_maximize()
                    # 最大化之后再算一次，把日志区拉到合适高度
                    self.update_idletasks()
                    win_h2 = self.winfo_height()
                    room2 = win_h2 - other - need
                    lines2 = max(6, int(room2 / 16))
                    self.txt.configure(height=lines2)
            except Exception:
                pass

            # 窗口尺寸变了，日志跟着滚到底
            self.after(50, lambda: self.txt.see("end") if self.var_auto.get() else None)
        else:
            self.adv.grid_remove()
            # 日志区恢复 10 行
            try:
                self.txt.configure(height=10)
            except Exception:
                pass
            # 如果展开时被最大化了，收起时退回普通窗口
            try:
                if self.state() == "zoomed":
                    self.state("normal")
            except Exception:
                pass
            if self._geo_before_adv is not None:
                w, h = self._geo_before_adv
                self._geo_before_adv = None
                self.geometry(f"{w}x{h}")

    def _sync_provider(self) -> None:
        raw = self.var_provider.get().split(" · ")[0].strip()
        tips = {
            "TikuGo": "GO题免费但有流控；它的 Token 在公众号「一之哥哥」申请，不填也能用（更慢）。",
            "TikuYanxi": "言溪题库按次收费，Token 填在下方 Token 框，多个用英文逗号分隔。",
            "TikuLike": "LIKE 知识库按次收费，Token 填下方；它还能做图片题。",
            "TikuAdapter": "TikuAdapter 是开源自建服务，把它的地址填到「题库自建地址」。",
            "AI": "填 OpenAI 兼容接口（如 https://api.deepseek.com/v1）＋ Key ＋ 模型名。",
            "SiliconFlow": "填硅基流动的 Key 和模型名，例如 deepseek-ai/DeepSeek-V3。",
            "TikuManual": "运行到章节检测时会暂停等你输入答案，答完自动提交。",
        }
        self.lbl_tip.configure(text=tips.get(raw, ""))

    def _open_readme(self) -> None:
        path = os.path.join(ROOT, "总说明.md")
        if os.path.exists(path):
            os.startfile(path)  # noqa: S606
        else:
            messagebox.showinfo(APP_TITLE, "总说明.md 还没生成。")

    def _open_folder(self) -> None:
        os.startfile(ROOT)  # noqa: S606

    def _open_logfile(self) -> None:
        if os.path.exists(LOG_FILE):
            os.startfile(LOG_FILE)  # noqa: S606
        else:
            messagebox.showinfo(APP_TITLE, "还没有日志文件，先跑一次任务。")

    # ---------- 诊断包 ----------
    # 只有"值本身是凭据"的键才打码。注意 use_cookies / cookie 路径这类是开关或文件名，不算。
    _SECRET_KEYS = ("password", "passwd", "token", "secret",
                    "api_key", "apikey", "authorization")
    _SECRET_EXACT = {"key", "tokens", "siliconflow_key", "url", "username"}
    # 这些键即便含关键词也保留原值（否则排障时看不出用户开了哪些开关）
    _SAFE_KEYS = {"use_cookies", "cookie_path", "url_wiki"}

    def _is_secret_key(self, key: str) -> bool:
        k = key.strip().lower()
        if k in self._SAFE_KEYS:
            return False
        if k in self._SECRET_EXACT:
            return True
        return any(s in k for s in self._SECRET_KEYS)

    def _redact(self, text: str) -> str:
        """把密码/TOKEN/Cookie 之类的值打码，避免朋友把诊断包发出来时泄露。"""
        lines = []
        for line in text.splitlines():
            low = line.lower()
            # HTTP 头
            if any(k in low for k in ("cookie:", "set-cookie:", "authorization:")):
                head = line.split(":", 1)[0]
                lines.append(f"{head}: ***已隐藏***")
                continue
            # ini 风格的 key = value
            m = re.match(r"^(\s*)([A-Za-z0-9_]+)(\s*=\s*)(.+)$", line)
            if m and self._is_secret_key(m.group(2)):
                lines.append(f"{m.group(1)}{m.group(2)}{m.group(3)}***已隐藏***")
                continue
            # 日志里偶发的 url 型 token（?token=xxx）
            line = re.sub(r"([?&](?:token|key|sign|ticket)=)[^&\s]+",
                          r"\1***", line, flags=re.IGNORECASE)
            lines.append(line)
        return "\n".join(lines)

    def _export_diag(self) -> None:
        """一键导出诊断包：环境信息 + 脱敏后的日志与配置，方便朋友回传。"""
        import zipfile

        stamp = time.strftime("%Y%m%d-%H%M%S")
        out_dir = os.path.join(ROOT, "诊断包")
        os.makedirs(out_dir, exist_ok=True)
        zip_path = os.path.join(out_dir, f"诊断-{stamp}.zip")

        info = [
            f"导出时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"程序目录：{ROOT}",
            f"Python：{PYTHON}",
            f"Python 存在：{os.path.exists(PYTHON)}",
            f"config.ini 存在：{os.path.exists(CONFIG)}",
            f"cookies.txt 存在：{os.path.exists(COOKIE)}",
            f"chaoxing.log 存在：{os.path.exists(LOG_FILE)}",
            "",
            "已生效补丁：",
            f"  {self.var_patch.get()}",
            "",
            "运行状态：",
            f"  状态={self.var_state.get()}  课程={self.var_course.get()}",
            f"  章节={self.var_chapter.get()}  已完成={self.var_done.get()}",
            "",
            "依赖检查：",
        ]
        # 依赖版本
        try:
            import importlib
            for mod in ("requests", "loguru", "bs4", "lxml", "pyaes",
                        "fontTools", "tqdm", "httpx", "urllib3", "chardet",
                        "tenacity", "openai", "ddddocr"):
                try:
                    m = importlib.import_module(mod)
                    info.append(f"  {mod:12} {getattr(m, '__version__', 'ok')}")
                except Exception as exc:
                    info.append(f"  {mod:12} 缺失（{type(exc).__name__}）")
        except Exception as exc:
            info.append(f"  依赖检查失败：{exc}")

        added = []
        try:
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("环境信息.txt", "\n".join(info))

                if os.path.exists(LOG_FILE):
                    try:
                        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                            raw = f.read()
                        # 只带最后 2000 行，够定位问题
                        tail = "\n".join(raw.splitlines()[-2000:])
                        zf.writestr("chaoxing.log(尾部2000行).txt", self._redact(tail))
                        added.append("日志")
                    except OSError:
                        pass

                if os.path.exists(CONFIG):
                    try:
                        with open(CONFIG, "r", encoding="utf-8", errors="replace") as f:
                            zf.writestr("config.ini(已脱敏).txt", self._redact(f.read()))
                        added.append("配置")
                    except OSError:
                        pass

                # 界面当前显示的内容也算证据
                try:
                    ui_text = self.txt.get("1.0", "end")
                    zf.writestr("界面日志.txt", self._redact(ui_text[-60000:]))
                    added.append("界面日志")
                except Exception:
                    pass
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"导出失败：{exc}")
            return

        size = os.path.getsize(zip_path) / 1024
        self._append(f"[{time.strftime('%H:%M:%S')}] 诊断包已导出：{zip_path}"
                     f"（{size:.0f} KB，含 {'、'.join(added) or '环境信息'}，密码/Cookie 已打码）", "ok")
        if messagebox.askyesno(
                APP_TITLE,
                f"诊断包已生成：\n{zip_path}\n\n"
                "里面包含：环境信息、日志尾部、脱敏后的配置。\n"
                "账号密码、Cookie、TOKEN 都已替换为 ***。\n\n"
                "现在打开所在文件夹吗？"):
            try:
                os.startfile(out_dir)  # noqa: S606
            except OSError:
                pass

    def _clear_log(self) -> None:
        self.txt.configure(state="normal")
        self.txt.delete("1.0", "end")
        self.txt.configure(state="disabled")

    def _append(self, text: str, tag: str | None = None) -> None:
        self.txt.configure(state="normal")
        self.txt.insert("end", text + "\n", tag or ())
        # 只保留最后 4000 行，避免长任务把内存吃满
        lines = int(self.txt.index("end-1c").split(".")[0])
        if lines > 4000:
            self.txt.delete("1.0", f"{lines - 4000}.0")
        self.txt.configure(state="disabled")
        if self.var_auto.get():
            self.txt.see("end")

    def _guess_tag(self, line: str) -> str | None:
        low = line.lower()
        if "error" in low or "错误" in line or "失败" in line or "异常" in line:
            return "err"
        if "warning" in low or "警告" in line or "重试" in line:
            return "warn"
        if "success" in low or "完成" in line or "成功" in line:
            return "ok"
        if "debug" in low or "trace" in low:
            return "dim"
        return None

    def _drain_log(self) -> None:
        if not self._alive:
            return
        try:
            for _ in range(400):
                line = self.log_queue.get_nowait()
                out = self._fold_traceback(line)
                if out is None:
                    continue
                self._append(out, self._guess_tag(out))
                self._parse_status(out)
        except queue.Empty:
            pass
        try:
            self.after(120, self._drain_log)
        except tk.TclError:
            pass

    _LOG_HEAD = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}[.\d]* \|")

    def _fold_traceback(self, line: str):
        """上游把整段 traceback 塞进一条 logger.error，这里按行收到。

        策略：
          - 带时间戳的是新日志 → 正常输出；若上一次还在折叠，先补一句汇总
          - 不带时间戳的是上一行的续行（堆栈体）→ 计数并丢弃
        """
        if self._LOG_HEAD.match(line) or line.startswith("__EXIT__"):
            tail = None
            if self._tb_count:
                tail = f"    ↳ 上面省略 {self._tb_count} 行异常堆栈（完整内容见 chaoxing.log）"
                self._tb_count = 0
            if tail:
                self._append(tail, "dim")
            return line

        if not line.strip():
            return None

        self._tb_count += 1
        return None

    def _parse_status(self, line: str) -> None:
        m = re.search(r"当前章节[:：]\s*(.+)", line)
        if m:
            self.var_chapter.set(m.group(1).strip()[:70])
        m = re.search(r"开始学习课程[:：]\s*(.+)", line)
        if m:
            self.var_course.set(m.group(1).strip()[:70])
        if "已完成所有任务点" in line or "任务完成" in line:
            self.done_chapters += 1
            self.var_done.set(f"{self.done_chapters} 章")

        # 补丁生效情况：显示在状态栏，方便朋友反馈时一眼看出跑的是哪版逻辑
        if line.startswith("[补丁] 共生效"):
            self.var_patch.set(line.replace("[补丁] 共生效", "").strip(" ：:"))
        elif "没有补丁生效" in line:
            # 这代表补丁全部跳过了，多半是内核版本变了 —— 要让用户看见，不能沉默
            self.var_patch.set("⚠ 未生效（内核可能已升级）")
            self._append(f"[{time.strftime('%H:%M:%S')}] "
                         "注意：缺陷补丁没有生效，可能是 core\\ 内核版本变了。"
                         "程序仍可运行，但「视频算不算看完」「卡死」等问题可能复现。", "warn")

        # 进度条式信息：tqdm 的 "xx%|" 或上游的进度日志
        m = re.search(r"(\d{1,3})%\|", line)
        if m:
            self.var_progress.set(f"{m.group(1)}%")

        # 章节失败预警：让用户早点知道要手动处理
        if "重试次数达到上限" in line or "请手动检查" in line:
            self.var_state.set("有章节失败（见日志）")

    def _tick(self) -> None:
        if not self._alive:
            return
        if self.running:
            sec = int(time.time() - self.start_ts)
            self.var_timer.set(f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}")
        try:
            self.after(500, self._tick)
        except tk.TclError:
            pass

    # ---------- 配置 ----------
    def _load_config(self) -> None:
        self.cfg = iniio.load(CONFIG, TEMPLATE)
        common = self.cfg.setdefault("common", {})
        tiku = self.cfg.setdefault("tiku", {})
        note = self.cfg.setdefault("notification", {})

        self.ent_user.insert(0, "" if common.get("username", "") == "xxx" else common.get("username", ""))
        self.ent_pass.insert(0, "" if common.get("password", "") == "xxx" else common.get("password", ""))
        self.var_cookie_login.set(common.get("use_cookies", "false").lower() == "true")

        courses = common.get("course_list", "")
        if courses and courses != "xxx,xxx,xxx":
            self.ent_courses.insert(0, courses)

        try:
            self.var_jobs.set(int(common.get("jobs", 2)))
        except ValueError:
            self.var_jobs.set(2)
        try:
            self.var_retry.set(float(common.get("retry_interval", 3.0)))
        except ValueError:
            self.var_retry.set(3.0)

        action = common.get("notopen_action", "retry")
        self.var_notopen.set(next((f"{k} · {v}" for k, v in NOTOPEN if k == action),
                                  f"retry · {NOTOPEN[0][1]}"))

        self.var_learn_count.set(common.get("add_learning_count", "false").lower() == "true")
        try:
            self.var_target.set(int(common.get("target_count", 100)))
        except ValueError:
            self.var_target.set(100)

        provider = tiku.get("provider", "TikuGo").split(",")[0].strip()
        # 模板里的默认值是付费的 TikuYanxi；没 token 时等于空转。
        # 首次运行（配置不存在）统一落到免费的 GO题。
        if not os.path.exists(CONFIG) or provider in ("", "TikuYanxi"):
            if not tiku.get("tokens", "").strip():
                provider = "TikuGo"
        self.var_provider.set(next((f"{k} · {v}" for k, v in PROVIDERS if k == provider),
                                   f"TikuGo · {PROVIDERS[0][1]}"))

        self.ent_token.insert(0, tiku.get("tokens", "") or tiku.get("key", "")
                              or tiku.get("siliconflow_key", ""))

        endpoint = (tiku.get("endpoint", "") or tiku.get("siliconflow_endpoint", "")
                    or tiku.get("url", ""))
        self.ent_endpoint.insert(0, endpoint)
        model = tiku.get("model", "") or tiku.get("siliconflow_model", "")
        self.ent_model.insert(0, model)
        if tiku.get("url"):
            self.ent_tiku_url.insert(0, tiku["url"])

        # 模板默认 submit=false（只保存不提交）。不提交则章节检测解锁不了，
        # 后续章节会全部"未开放"。首次运行按"达到覆盖率就提交"处理。
        submit_raw = tiku.get("submit", "true").strip().lower()
        if not os.path.exists(CONFIG):
            submit_raw = "true"
        self.var_submit.set(submit_raw == "true")
        try:
            self.var_cover.set(int(round(float(tiku.get("cover_rate", 0.9)) * 100)))
        except ValueError:
            self.var_cover.set(90)
        try:
            self.var_delay.set(float(tiku.get("delay", 1.0)))
        except ValueError:
            self.var_delay.set(1.0)

        nprov = (note.get("provider", "") or "").strip()
        # 模板默认 ServerChan，但没填 url 等于没配；首次运行落在"不推送"。
        if nprov == "ServerChan" and not note.get("url", "").strip():
            nprov = ""
        self.var_notify.set(next((f"{k} · {v}" if k else v for k, v in NOTIFIERS if k == nprov),
                                 NOTIFIERS[0][1]))
        self.ent_notify.insert(0, note.get("url", ""))

        self._toggle_cookie()
        self._sync_provider()
        # 增强开关（文档翻阅/链接任务/人脸通知）是独立小文件，最后读回来
        self._load_enhance_flags()
        self._build_status_hint()

    def _build_status_hint(self) -> None:
        """首屏给一句可执行的提示，避免用户对着空日志发愣。"""
        parts = ["准备就绪。"]
        if not os.path.exists(PYTHON):
            parts.append("⚠ 运行环境缺失，请重新解压安装包。")
        if not os.path.exists(CONFIG):
            parts.append("首次使用：填账号 → 点「开始刷课」。不填课程 ID 会在运行时列出课程。")
        self._append(" ".join(parts), "dim")
        if os.path.exists(LOG_FILE):
            self._append(f"上一次的完整日志在 chaoxing.log（右上「打开完整日志」）。", "dim")

    def _collect(self) -> dict[str, dict[str, str]]:
        provider = self.var_provider.get().split(" · ")[0].strip()
        notopen = self.var_notopen.get().split(" · ")[0].strip()
        notify = self.var_notify.get()
        notify_key = notify.split(" · ")[0].strip() if " · " in notify else ""

        tiku: dict[str, str] = {
            "provider": provider,
            "submit": "true" if self.var_submit.get() else "false",
            "cover_rate": f"{max(0, min(100, self.var_cover.get())) / 100:.2f}",
            "delay": f"{self.var_delay.get():.1f}",
        }
        token = self.ent_token.get().strip()
        if provider in ("AI",):
            tiku["key"] = token
            tiku["endpoint"] = self.ent_endpoint.get().strip()
            tiku["model"] = self.ent_model.get().strip()
        elif provider == "SiliconFlow":
            tiku["siliconflow_key"] = token
            tiku["siliconflow_model"] = self.ent_model.get().strip() or "deepseek-ai/DeepSeek-V3"
            tiku["siliconflow_endpoint"] = (self.ent_endpoint.get().strip()
                                            or "https://api.siliconflow.cn/v1/chat/completions")
        elif provider == "TikuAdapter":
            tiku["url"] = self.ent_tiku_url.get().strip() or self.ent_endpoint.get().strip()
        else:
            tiku["tokens"] = token

        return {
            "common": {
                "use_cookies": "true" if self.var_cookie_login.get() else "false",
                "username": self.ent_user.get().strip(),
                "password": self.ent_pass.get(),
                "course_list": self.ent_courses.get().strip(),
                "jobs": str(max(1, min(12, self.var_jobs.get()))),
                "notopen_action": notopen,
                "retry_interval": f"{self.var_retry.get():.1f}",
                "add_learning_count": "true" if self.var_learn_count.get() else "false",
                "target_count": str(self.var_target.get()),
            },
            "tiku": tiku,
            "notification": {
                "provider": notify_key,
                "url": self.ent_notify.get().strip(),
            },
        }

    def _save_enhance_flags(self) -> None:
        """把三个增强开关写到 app\\enhance_flags.json。

        为什么不写进 config.ini：
            config.ini 是**上游内核的配置**，它的字段由 core/api/config.py 解析，
            塞进去未知字段不会报错但也不会生效。
            enhance.py 是我们在进程内打补丁的模块，它读自己的小文件最直接，
            而且改完不用重启内核进程（每次启动读一次）。
        """
        import json
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "enhance_flags.json")
        flags = {
            "document_turn": bool(self.var_enh_doc.get()),
            "link_task": bool(self.var_enh_link.get()),
            "face_notify": bool(self.var_enh_face.get()),
        }
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(flags, f, ensure_ascii=False, indent=2)
        except OSError as exc:
            self._append(f"保存增强开关失败：{exc}", "err")

    def _load_enhance_flags(self) -> None:
        """启动时读回上次勾选状态。"""
        import json
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "enhance_flags.json")
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                flags = json.load(f)
            self.var_enh_doc.set(bool(flags.get("document_turn", True)))
            self.var_enh_link.set(bool(flags.get("link_task", True)))
            self.var_enh_face.set(bool(flags.get("face_notify", True)))
        except Exception:
            pass


    def _save_config(self) -> None:
        try:
            values = self._collect()
            os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
            iniio.save(CONFIG, TEMPLATE, values)
            self.cfg = values
            # 增强开关单独存（不进 config.ini，原因见 _save_enhance_flags 的注释）
            self._save_enhance_flags()
            self._append(f"[{time.strftime('%H:%M:%S')}] 设置已保存到 config.ini", "ok")
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"保存失败：{exc}")

    # ---------- 任务 ----------
    def _need_login(self) -> bool:
        if self.var_cookie_login.get():
            if not os.path.exists(COOKIE):
                messagebox.showwarning(
                    APP_TITLE,
                    "勾了 Cookie 登录但 core\\cookies.txt 不存在。\n\n"
                    "点左侧「粘贴 Cookie」，从浏览器复制后粘进来即可。")
                return False
            return True
        if not self.ent_user.get().strip() or not self.ent_pass.get():
            messagebox.showwarning(APP_TITLE, "请填手机号和密码，或勾选 Cookie 登录。")
            return False
        return True

    def _list_courses(self) -> None:
        """调用内核的课程列表接口，把课程名和 ID 打出来。"""
        if not self._need_login():
            return
        self._save_config()
        script = os.path.join(ROOT, "app", "list_courses.py")
        if not os.path.exists(script):
            messagebox.showinfo(APP_TITLE, "缺少 app\\list_courses.py。")
            return
        self._append(f"[{time.strftime('%H:%M:%S')}] 正在读取课程列表…", "dim")
        threading.Thread(target=self._run_list_courses, args=(script,), daemon=True).start()

    def _run_list_courses(self, script: str) -> None:
        import subprocess

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        env["PYTHONUNBUFFERED"] = "1"
        found: list[tuple[str, str]] = []   # [(courseId, title)]
        try:
            cp = subprocess.run(
                [PYTHON, script],
                cwd=CORE, capture_output=True, timeout=300, env=env,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            out = (cp.stdout or b"").decode("utf-8", "replace")
            err = (cp.stderr or b"").decode("utf-8", "replace")
            for line in out.splitlines():
                if line.startswith("COURSE|"):
                    parts = line.split("|")
                    if len(parts) == 3:
                        title, cid = parts[1].strip(), parts[2].strip()
                        if cid:
                            found.append((cid, title))
                        self.log_queue.put(f"  课程 {title}  ID={cid}")
                elif line.strip():
                    self.log_queue.put(line)
            if cp.returncode != 0:
                for line in err.splitlines()[-12:]:
                    self.log_queue.put(f"[错误] {line}")

            if found:
                self.log_queue.put(f"读取到 {len(found)} 门课程，请在弹出的窗口里勾选要刷的课。")
                if self._alive:
                    # 主线程里弹窗（Tkinter 控件只能在主线程操作）
                    self.after(0, self._show_course_picker, found)
            else:
                self.log_queue.put("没有读到可用课程。若账号确实有课，请看上面是否有报错。")
        except subprocess.TimeoutExpired:
            self.log_queue.put("[错误] 读取课程超时（5 分钟）。检查网络后重试。")
        except Exception as exc:
            self.log_queue.put(f"[错误] 读取课程失败：{exc}")

    def _show_course_picker(self, courses: list[tuple[str, str]]) -> None:
        """把识别到的课程列成可勾选清单，让用户自己决定刷哪几门。

        之前是"识别完直接填 ID、全部开刷"，朋友 47 门课一次全跑起来了，
        完全没有选择的余地。这里补上真正的勾选界面。
        """
        if not self._alive:
            return

        win = tk.Toplevel(self)
        win.title("选择要刷的课程")
        win.geometry("720x520")
        win.transient(self)
        win.grab_set()

        ttk.Label(
            win,
            text=f"共 {len(courses)} 门课程。勾选你要刷的，然后点「确定」。",
            font=("Microsoft YaHei UI", 10, "bold"),
        ).pack(anchor="w", padx=14, pady=(14, 4))
        ttk.Label(
            win,
            text="提示：一次刷太多门容易被风控。建议一次选 1–3 门，刷完再选下一批。",
            style="Sub.TLabel",
        ).pack(anchor="w", padx=14, pady=(0, 8))

        # 工具条
        bar = ttk.Frame(win)
        bar.pack(fill="x", padx=14, pady=(0, 6))
        ttk.Button(bar, text="全选", style="Small.TButton",
                   command=lambda: _set_all(True)).pack(side="left")
        ttk.Button(bar, text="全不选", style="Small.TButton",
                   command=lambda: _set_all(False)).pack(side="left", padx=(6, 0))

        # 可滚动列表
        wrap = ttk.Frame(win)
        wrap.pack(fill="both", expand=True, padx=14)
        canvas = tk.Canvas(wrap, highlightthickness=0)
        vsb = ttk.Scrollbar(wrap, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        win_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfigure(win_id, width=e.width))

        def _wheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        canvas.bind_all("<MouseWheel>", _wheel)

        vars_: list[tuple[tk.BooleanVar, str]] = []
        # 已经填在输入框里的 ID 默认勾上
        pre = {x.strip() for x in self.ent_courses.get().split(",") if x.strip()}

        for i, (cid, title) in enumerate(courses):
            v = tk.BooleanVar(value=(cid in pre) or (not pre and i < 3))
            vars_.append((v, cid))
            row = ttk.Frame(inner)
            row.pack(fill="x", anchor="w", pady=1)
            ttk.Checkbutton(row, variable=v).pack(side="left")
            ttk.Label(row, text=title or "(无标题)", width=44).pack(side="left", padx=(2, 0))
            ttk.Label(row, text=f"ID {cid}", style="Sub.TLabel").pack(side="left", padx=(6, 0))

        def _set_all(val: bool) -> None:
            for v, _cid in vars_:
                v.set(val)

        def _confirm() -> None:
            ids = [cid for v, cid in vars_ if v.get()]
            canvas.unbind_all("<MouseWheel>")
            win.destroy()
            if not ids:
                messagebox.showwarning(APP_TITLE, "一门课都没勾。要刷全部就点「全选」。")
                return
            self.ent_courses.delete(0, "end")
            self.ent_courses.insert(0, ",".join(ids))
            self._save_config()
            self._append(
                f"[{time.strftime('%H:%M:%S')}] 已选 {len(ids)} 门课程，可以点「开始刷课」了", "ok")

        foot = ttk.Frame(win)
        foot.pack(fill="x", padx=14, pady=(8, 14))
        ttk.Button(foot, text="确定（用这些课）", command=_confirm).pack(side="right")
        ttk.Button(foot, text="取消", command=lambda: (canvas.unbind_all("<MouseWheel>"),
                                                      win.destroy())).pack(side="right", padx=(0, 8))
        ttk.Label(foot, text="勾选结果会写进上方课程输入框", style="Sub.TLabel").pack(side="left")

    def _append_course_id(self, cid: str) -> None:
        cur = self.ent_courses.get().strip()
        ids = [x.strip() for x in cur.split(",") if x.strip()]
        if cid not in ids:
            ids.append(cid)
            self.ent_courses.delete(0, "end")
            self.ent_courses.insert(0, ",".join(ids))

    def _start(self) -> None:
        if self.running:
            return
        if not self._need_login():
            return
        if not os.path.exists(PYTHON):
            messagebox.showerror(APP_TITLE, f"找不到运行环境：\n{PYTHON}\n\n请重新解压完整安装包。")
            return
        if not os.path.exists(os.path.join(CORE, "main.py")):
            messagebox.showerror(APP_TITLE, "core\\main.py 缺失，安装包不完整。")
            return

        # 课程 ID 留空时内核会切到 stdin 交互，而我们接的是 DEVNULL，会直接崩。
        # 更重要的是：绝不能"留空即刷全部" —— 上一个版本就是这么干的，
        # 朋友账号 47 门课一次性全开跑了，他连选的机会都没有。
        if not self.ent_courses.get().strip():
            if not messagebox.askyesno(
                    APP_TITLE,
                    "还没选课程。\n\n"
                    "是否现在去识别课程并勾选？（推荐）\n\n"
                    "点「是」→ 读取课程列表，弹出勾选窗口\n"
                    "点「否」→ 返回自己手动填课程 ID"):
                return
            self._list_courses()
            self._append(
                f"[{time.strftime('%H:%M:%S')}] 正在读取课程列表，读完后会弹出勾选窗口；"
                "选好课程再点「开始刷课」。", "dim")
            return

        self._save_config()
        self._clear_log()
        self.running = True
        self.start_ts = time.time()
        self.done_chapters = 0
        self.var_paused.set(False)
        self.var_state.set("运行中")
        self.var_course.set("—")
        self.var_chapter.set("—")
        self.btn_start.configure(state="disabled")
        self.btn_pause.configure(state="normal", text="⏸  暂停")
        self.btn_stop.configure(state="normal")
        self._append(f"[{time.strftime('%H:%M:%S')}] 启动内核…", "ok")

        self.runner = Runner(
            python_exe=PYTHON,
            cwd=CORE,
            config_path="config.ini",
            on_line=self.log_queue.put,
            on_exit=self._on_runner_exit,
        )
        try:
            self.runner.start()
        except Exception as exc:
            self.running = False
            self.var_state.set("启动失败")
            self.btn_start.configure(state="normal")
            self.btn_stop.configure(state="disabled")
            messagebox.showerror(APP_TITLE, f"启动失败：{exc}")

    def _on_runner_exit(self, code: int) -> None:
        if not self._alive:
            return
        try:
            self.after(0, self._finish, code)
        except tk.TclError:
            pass

    def _finish(self, code: int) -> None:
        if not self._alive:
            return
        self.running = False
        self._clear_pause_flag()
        self.var_paused.set(False)
        self.var_state.set("已完成" if code == 0 else f"已结束（代码 {code}）")
        self.btn_start.configure(state="normal")
        self.btn_pause.configure(state="disabled", text="⏸  暂停")
        self.btn_stop.configure(state="disabled")
        self._append(f"[{time.strftime('%H:%M:%S')}] 任务结束，退出码 {code}", 
                     "ok" if code == 0 else "err")

    def _stop(self) -> None:
        if not self.runner:
            return
        # 停止前一定清掉暂停标记，否则下次启动会一上来就卡住
        self._clear_pause_flag()
        self.var_state.set("正在停止…")
        self.btn_stop.configure(state="disabled")
        self.btn_pause.configure(state="disabled")
        threading.Thread(target=self._do_stop, daemon=True).start()

    # ---------- 暂停 / 继续 ----------
    def _pause_flag_path(self) -> str:
        if self.runner and self.runner.pause_flag:
            return self.runner.pause_flag
        return os.path.join(ROOT, "pause.flag")

    def _clear_pause_flag(self) -> None:
        p = self._pause_flag_path()
        try:
            if os.path.exists(p):
                os.remove(p)
        except OSError:
            pass

    def _toggle_pause(self) -> None:
        """暂停/继续：写或删标记文件，内核会在下次检查点停下/继续。

        注意：不是挂起进程。挂起的话真实时间还在走，恢复后会把暂停那几分钟
        全算成「已观看」，进度虚高会被平台判异常。这里让内核在代码里等，
        等的时间从观看时长里扣掉。
        """
        if not self.running:
            return
        p = self._pause_flag_path()
        try:
            if os.path.exists(p):
                os.remove(p)
                self.var_paused.set(False)
                self.var_state.set("运行中")
                self.btn_pause.configure(text="⏸  暂停")
                self._append(f"[{time.strftime('%H:%M:%S')}] 已请求继续（内核在下一个检查点恢复）", "ok")
            else:
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "w", encoding="utf-8") as f:
                    f.write(str(int(time.time())))
                self.var_paused.set(True)
                self.var_state.set("已暂停")
                self.btn_pause.configure(text="▶  继续")
                self._append(f"[{time.strftime('%H:%M:%S')}] 已请求暂停"
                             "（内核处理完当前请求后停下，暂停时间不计入观看时长）", "warn")
        except OSError as exc:
            messagebox.showerror(APP_TITLE, f"操作失败：{exc}")

    def _do_stop(self) -> None:
        if self.runner:
            self.runner.stop()
        if not self._alive:
            return
        try:
            self.after(0, self._finish, -1)
        except tk.TclError:
            pass

    def _force_quit(self) -> None:
        """强制退出：不等待内核优雅关闭，直接杀进程然后关窗口。

        什么时候用：
            内核卡在某个卡死的网络请求里，或者工作线程死锁，
            点「停止」后状态一直停在「正在停止…」，窗口关不掉。
            这时候用这个按钮，一步到位。

        和「停止」的区别：
            停止 = 请求内核自己退出，正常情况 1-2 秒，会保存进度
            强制退出 = 立刻 taskkill /F /T，可能丢最后一条进度上报，
                       但保证能退出
        """
        if not messagebox.askyesno(
                APP_TITLE,
                "强制退出会立即终止所有子进程。\n\n"
                "如果正在刷课，最后一条进度可能来不及上报。\n\n"
                "确定要强制退出吗？"):
            return

        self.var_state.set("正在强制退出…")
        self._append(f"[{time.strftime('%H:%M:%S')}] 用户请求强制退出", "warn")

        # 先清暂停标记，否则下次启动会一上来就卡住
        self._clear_pause_flag()

        # 强杀子进程（不等它优雅退出）
        try:
            if self.runner:
                proc = getattr(self.runner, "proc", None)
                if proc is not None and proc.poll() is None:
                    if os.name == "nt":
                        subprocess.run(
                            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                            capture_output=True,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                        )
                    else:
                        proc.kill()
                    self._append("已强制终止内核进程", "warn")
        except Exception as exc:
            self._append(f"终止进程时出错（忽略继续退出）：{exc}", "err")

        # 存一次配置（失败也无所谓，不能让保存卡住退出）
        try:
            self._save_config()
        except Exception:
            pass

        self._alive = False
        try:
            self.destroy()
        except tk.TclError:
            pass
        # 界面没了但进程还活着的情况（极少数）→ 兜底硬退
        os._exit(0)

    def _on_close(self) -> None:
        if self.running:
            if not messagebox.askyesno(APP_TITLE, "任务还在跑，确定要停止并退出吗？"):
                return
            if self.runner:
                self.runner.stop()
        try:
            self._save_config()
        except Exception:
            pass
        self._alive = False
        self.destroy()


def main() -> None:
    if not os.path.exists(os.path.join(CORE, "main.py")):
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(APP_TITLE, "安装目录不完整：缺少 core\\main.py")
        return
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
