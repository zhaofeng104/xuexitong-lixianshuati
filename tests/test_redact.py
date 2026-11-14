# -*- coding: utf-8 -*-
"""诊断包脱敏逻辑测试 —— 这是本项目最后一道隐私防线。

`app/ui.py` 的 `_redact()` 负责在导出诊断包前把凭据打码，
一旦正则漏掉某个字段，用户把诊断包发给别人就等于交出账号。

本测试不依赖 Tkinter：用 `__new__` 绕过 `__init__`，直接调用未绑定方法。

跑法（在项目根目录）：
    _runtime\\python.exe -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))


def _load_redact():
    """取出 MainWindow._redact 与 _is_secret_key，不实例化 Tk 窗口。"""
    import ui  # app/ 已在 sys.path

    cls = None
    for name in dir(ui):
        obj = getattr(ui, name)
        if isinstance(obj, type) and hasattr(obj, "_redact"):
            cls = obj
            break
    if cls is None:
        raise RuntimeError("在 app/ui.py 里找不到带 _redact 的类")

    inst = cls.__new__(cls)  # 不跑 __init__，避开 Tkinter
    return inst._redact, inst._is_secret_key


class TestRedactHeaders(unittest.TestCase):
    """HTTP 头整行打码。"""

    @classmethod
    def setUpClass(cls):
        cls.redact, _ = _load_redact()

    def test_cookie_header(self):
        src = "Cookie: _uid=123456; uf=abcdefg; p_auth_token=zzz"
        out = self.redact(src)
        self.assertNotIn("_uid=123456", out)
        self.assertNotIn("p_auth_token", out.replace("Cookie:", ""))
        self.assertIn("***", out)

    def test_set_cookie_header(self):
        out = self.redact("Set-Cookie: JSESSIONID=deadbeef; Path=/")
        self.assertNotIn("deadbeef", out)

    def test_authorization_header(self):
        out = self.redact("Authorization: Bearer sk-abcdef123456")
        self.assertNotIn("sk-abcdef123456", out)


class TestRedactIniStyle(unittest.TestCase):
    """ini 风格 key = value。"""

    @classmethod
    def setUpClass(cls):
        cls.redact, _ = _load_redact()

    def test_password(self):
        out = self.redact("password = MyRealPass123")
        self.assertNotIn("MyRealPass123", out)
        self.assertIn("password", out)

    def test_token(self):
        out = self.redact("tokens = tk_live_abcdefg")
        self.assertNotIn("tk_live_abcdefg", out)

    def test_api_key(self):
        out = self.redact("api_key = sk-proj-12345")
        self.assertNotIn("sk-proj-12345", out)

    def test_indented_key(self):
        """带缩进的配置行也要打码。"""
        out = self.redact("    password = DeepPass")
        self.assertNotIn("DeepPass", out)


class TestRedactUrlToken(unittest.TestCase):
    """URL 查询串里的 token。"""

    @classmethod
    def setUpClass(cls):
        cls.redact, _ = _load_redact()

    def test_query_token(self):
        out = self.redact("GET /api?token=secret123&page=1")
        self.assertNotIn("secret123", out)
        self.assertIn("page=1", out)  # 非敏感参数要保留

    def test_query_key(self):
        out = self.redact("https://x.com/a?key=abcdef&sign=zzz&ticket=qqq")
        for v in ("abcdef", "zzz", "qqq"):
            self.assertNotIn(v, out)


class TestSafeKeysPreserved(unittest.TestCase):
    """开关类键不能被误伤，否则诊断包看不出用户开了什么。"""

    @classmethod
    def setUpClass(cls):
        cls.redact, cls.is_secret = _load_redact()

    def test_use_cookies_is_safe(self):
        self.assertFalse(self.is_secret("use_cookies"))

    def test_cookie_path_is_safe(self):
        self.assertFalse(self.is_secret("cookie_path"))

    def test_use_cookies_line_not_redacted(self):
        out = self.redact("use_cookies = true")
        self.assertIn("true", out)

    def test_plain_line_untouched(self):
        out = self.redact("章节：第一章 绪论")
        self.assertEqual(out, "章节：第一章 绪论")


class TestRegressionRealLeak(unittest.TestCase):
    """回归：LESSONS.md S1 记录过的那类泄露必须被打码。"""

    @classmethod
    def setUpClass(cls):
        cls.redact, _ = _load_redact()

    def test_loguru_session_dump(self):
        """loguru 打印的 session 对象不带 'cookie:' 前缀，必须照样打码。"""
        src = (
            "2026-11-09 10:00:00 | DEBUG | Logged in with cookies: "
            "<RequestsCookieJar[_uid=123, uf=abcdef]>"
        )
        out = self.redact(src)
        self.assertNotIn("_uid=123", out)
        self.assertNotIn("abcdef", out)
        self.assertIn("已隐藏", out)

    def test_loguru_plain_text_kept(self):
        """普通日志行不能被 cookies 兜底规则误伤。"""
        src = "2026-11-09 10:00:00 | INFO | 章节：第一章 已完成所有任务点"
        out = self.redact(src)
        self.assertEqual(out, src)

    def test_mixed_block(self):
        block = "\n".join([
            "导出时间：2026-11-09",
            "Cookie: _uid=1; uf=2",
            "password = hunter2",
            "url = https://example.com",
            "use_cookies = true",
        ])
        out = self.redact(block)
        self.assertNotIn("_uid=1", out)
        self.assertNotIn("hunter2", out)
        self.assertIn("2026-11-09", out)
        self.assertIn("use_cookies = true", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
