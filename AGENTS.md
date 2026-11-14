# AGENTS.md　协作与工程约定

给 AI 编码助手（以及未来的协作者）的硬规则。
**用户与系统的当轮指令优先级高于本文件。**

配套文档：
- [`LESSONS.md`](LESSONS.md) —— 已证实的踩坑与防错规约
- [`NOTICE.md`](NOTICE.md) —— 第三方版权与许可
- [`CHANGELOG.md`](CHANGELOG.md) —— 版本变更

---

## 一、许可证红线（最高优先级）

本项目是 GPL-3.0 衍生作品，**这不是可选项**。

| 禁止 | 说明 |
|---|---|
| ❌ 闭源商业发布 / 销售 | 上游作者明确禁止 |
| ❌ 任何形式盈利 | 无广告、无付费墙、无捐赠入口、不卖授权 |
| ❌ 移除以 `skreon` / `SocialSisterYi` / `Samueli924` 署名的源文件头 | 版权声明不可删改 |
| ❌ 新增依赖 GPL 不兼容的库 | 引入前先查许可证 |

修改涉及版权文件的代码时，**保留原作者声明原样**，新增内容另起注释块说明由谁在何时改。

---

## 二、代码边界：`core/` 是上游，`app/` 是我们的

```
core/   ← GPL-3.0 上游衍生。不要重构、不要改结构、不要重命名函数
app/    ← 本项目原创。可自由重构、拆分、优化
```

**要改 `core/` 的行为，用「包一层」而不是「改源码」：**

```python
# app/patches.py 的既有模式
original = Chaoxing.some_method
def patched(self, *a, **kw):
    ...  # 前置处理
    return original(self, *a, **kw)
Chaoxing.some_method = patched
```

理由：直接改 `core/` 会让后续同步上游变成手工合并地狱。九项补丁全部按这个模式实现，
每项都有独立开关常量，可单独关闭定位问题。

**例外**：安全类修复（如 S1 日志泄露）可以直接改 `core/`，
但必须在 commit message 里说明原因，并在 `LESSONS.md` 记一条。

### 函数长度

- `app/` 下**超过 100 行**的函数应拆分（当前 3 个待处理：`make_dist.py::main` 157 行等）
- `core/` 下的超长函数**保持原样**（`base.py::study_work` 288 行），不要动
- 交付前复查改动函数的**嵌套层级和闭包体**，不能以「测试通过」代替代码可读性检查

---

## 三、安全与隐私

### 日志

**凭据绝不能进入日志格式串。** 内核日志固定 `level="TRACE"` 并落盘，
任何 `logger.debug` 都会写进 `chaoxing.log`（10 MB 滚动）。

```python
# ❌ 绝对禁止
logger.debug(f"cookies: {session.cookies}")

# ✅ 只记元信息
logger.debug(f"cookies: {len(session.cookies)} keys")
```

涉及密码、Token、Cookie、API Key 的变量，打印前一律先问：
**这个对象的 `__str__` 会不会把值带出来？**

### 凭据文件

以下文件**永远不进版本控制、永远不进交付包**：

```
cookies.txt   config.ini   cache.json   *.log
```

`.gitignore` 与 `make_dist.py` 的 `EXCLUDE` 是两道拦截，打包后还要**真的打开 zip 复查**。

### 隐私承诺

界面或文档里写「不上传数据」之前，先对照代码核验实际外发链路。
当前已知四条：超星主站、`detect.chaoxing.com` 心跳、第三方题库、AI 接口。

---

## 四、提交与发布

### 提交信息

含中文 / 引号 / 多行时，**必须走文件传递**，不要拼进 `-m`：

```powershell
$msg = @"
fix(security): 不再把 session.cookies 写入日志
"@
[System.IO.File]::WriteAllText($msgFile, $msg, (New-Object System.Text.UTF8Encoding($false)))
git -c core.safecrlf=false commit -F $msgFile
```

格式：`type(scope): 简述`，type 取 `feat` / `fix` / `refactor` / `docs` / `chore`。
安全类修复用 `fix(security)`。

### 发布

| 步骤 | 命令 | 注意 |
|---|---|---|
| 打包 | `python make_release.py` | 产出 `dist/*.zip` 并自动核对 |
| 打 tag | `git tag vX.Y.Z` | 带 `v` 前缀 |
| 建 Release | `gh release create vX.Y.Z <zip>` | **文件名必须 ASCII** |
| 回读校验 | `gh release download ... && Get-FileHash` | 哈希一致才算完成 |

**发布完成的判据是「回读比对哈希」，不是命令退出码。**

---

## 五、不做无依据的防御

兜底、兼容、额外校验必须有**实际失败样本**或**已确认的业务约束**作为依据。

- ❌ `try: ... except: return None` 掩盖真实错误
- ✅ 失败沿外层既有处理传播，或打印带上下文的警告后再降级

静默返回 `None` / 空值只表达**预期的「没有」**。非预期状态要转成哨兵值继续跑时，
必须留下能诊断的日志。

**正例**：`app/patches.py` 的九项补丁每项都有实测数据支撑（如「36 分钟 11 个视频只完成 2 个」），
这是本项目该保持的风格。

---

## 六、工作流

### 改动前

1. 读 [`LESSONS.md`](LESSONS.md) 中与当前任务相关的条目
2. 确认改动落在 `app/` 还是 `core/` —— 决定用「重构」还是「包一层」
3. 涉及版权文件时，确认声明保留

### 改动后

1. 语法检查：`python -m py_compile <file>`
2. 若有交付影响，重新打包并**打开 zip 复查**
3. 更新 [`CHANGELOG.md`](CHANGELOG.md)
4. 如果是踩过的坑，更新 [`LESSONS.md`](LESSONS.md)

### 汇报

讲三件事：**做了什么 / 证据是什么 / 还剩什么没做**。
区分「已实测」与「推断」——没测过的不要说成测过。
