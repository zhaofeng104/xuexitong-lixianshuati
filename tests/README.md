# 测试说明

## 跑法

在项目根目录执行：

```bash
_runtime\python.exe -m unittest discover -s tests -v
```

需要 `_runtime\python.exe`（随交付包分发的独立 Python 3.13）。
若用系统 Python，改用自己的解释器路径即可，测试本身不依赖第三方库。

## 现有测试

| 文件 | 覆盖对象 | 说明 |
|---|---|---|
| `test_redact.py` | `app/ui.py` 的 `_redact()` 与 `_is_secret_key()` | 诊断包脱敏，**16 个用例** |

### 为什么先测这个

`_redact()` 是导出诊断包前的最后一道隐私防线：用户把诊断包发给别人时，
密码 / Token / Cookie 全靠它打码。正则漏一个字段，等于交出账号。

测试覆盖四类规则：

1. **HTTP 头** —— `Cookie:` / `Set-Cookie:` / `Authorization:` 整行打码
2. **ini 风格** —— `password = xxx`、`tokens = xxx`、带缩进的配置行
3. **URL 查询串** —— `?token=` / `?key=` / `?sign=` / `?ticket=`
4. **loguru 会话对象** —— `<RequestsCookieJar[_uid=1, uf=2]>` 这类
   既没有 `cookie:` 前缀、也不是 `key=value` 的格式（LESSONS.md S1 记录的泄露形态）

同时有反向断言：`use_cookies = true`、章节标题等**普通内容不能被误伤**，
否则诊断包会失去排障价值。

## 测试设计约定

- **不依赖 Tkinter**：`ui.py` 里的类用 `__new__` 绕过 `__init__`，
  只取未绑定方法，测试可以在无图形环境跑
- **不联网**：TLS、题库都不在测试范围内，需要真实请求的检查单独跑
- **断言要针对行为**：`assertNotIn("明文", out)` 而不是 `assertIsInstance(out, str)`
  —— 后者写多少都恒真，等于没测

## 待补

| 对象 | 值得测什么 |
|---|---|
| `app/patches.py::is_network_error()` | 各类异常是否被正确判定为网络错误 |
| `app/iniio.py` | ini 读写、缺失键的默认值、编码 |
| `app/enhance.py` | `enhance_flags.json` 缺字段/坏 JSON 时的行为 |
