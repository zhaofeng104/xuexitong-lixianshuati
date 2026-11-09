# 版权与许可声明 · NOTICE

本项目（学习通刷课助手 图形界面版）是多个开源项目的衍生成果。
**所有原始作者的版权声明完整保留，未做任何删改。** 以下按依赖层次逐项列明。

---

## 一、上游内核 · Samueli924/chaoxing

| 项 | 内容 |
|---|---|
| 项目 | [Samueli924/chaoxing](https://github.com/Samueli924/chaoxing) |
| 作者 | Samueli924 及所有贡献者 |
| 许可证 | **GNU GPL v3**（原文见 [`core/LICENSE`](core/LICENSE)） |
| 使用范围 | 本项目 `core/` 目录整体 |

### 上游作者明确的四条要求（原文照录）

> - 本代码遵循 [GPL-3.0 License](https://github.com/Samueli924/chaoxing/blob/main/LICENSE) 协议，
>   允许**开源/免费使用和引用/修改/衍生代码的开源/免费使用**，
>   不允许**修改和衍生的代码作为闭源的商业软件发布和销售**，
>   禁止**使用本代码盈利**，
>   以此代码为基础的程序**必须**同样遵守 [GPL-3.0 License](https://github.com/Samueli924/chaoxing/blob/main/LICENSE) 协议
> - 本代码仅用于**学习讨论**，禁止**用于盈利**
> - 他人或组织使用本代码进行的任何**违法行为**与本人无关

### 本项目的遵守情况

| 要求 | 本项目执行 |
|---|---|
| 衍生必须同为 GPL-3.0 | ✅ **整个仓库**以 GPL-3.0 发布，见根目录 [`LICENSE`](LICENSE) |
| 不允许闭源商业发布 | ✅ 源码完全公开，无闭源模块 |
| 禁止盈利 | ✅ **免费、无广告、无付费墙、无捐赠入口**，不销售任何形式的授权 |
| 仅限学习讨论 | ✅ 仅作技术学习用途，不提供任何商业服务 |

---

## 二、模块级第三方作者

以下作者在具体文件中持有独立版权声明，原声明**逐字保留在源文件头部**，此处汇总。

### 2.1 skreon —— 验证码识别模块

| 项 | 内容 |
|---|---|
| 文件 | [`core/api/captcha.py`](core/api/captcha.py) |
| 作者 | **skreon** |
| 联系 | 1340554713@qq.com |
| 原始日期 | 2025-06-05 |
| 原始版本 | 1.0.0 |
| 依赖 | [DdddOcr](https://github.com/sml2h3/ddddocr)（开源验证码识别库） |

源文件头部原样保留：

```python
"""
Captcha API for Chaoxing

本模块用于通过CX验证码，提供包括验证码获取、识别、验证等接口。
使用了开源的验证码识别库[DdddOcr](https://github.com/sml2h3/ddddocr)

Author: skreon
Email: 1340554713@qq.com
Date: 2025-06-05
Version: 1.0.0
"""

__author__ = "skreon 1340554713@qq.com"
__version__ = "1.0.0"
```

**本项目对这一模块的改动**：无。文件内容与上游一致，未做功能修改，仅随整体一起分发。

### 2.2 SocialSisterYi —— 缓存与字体解码

| 项 | 内容 |
|---|---|
| 涉及文件 | [`core/api/answer.py`](core/api/answer.py)（`CacheDAO` 类）<br>[`core/api/cxsecret_font.py`](core/api/cxsecret_font.py)（全文） |
| 作者 | **SocialSisterYi** |
| 二次修改 | Samueli924 |
| 参考来源 | [xuexiaoyi-to-xuexitong-tampermonkey-proxy](https://github.com/SocialSisterYi/xuexiaoyi-to-xuexitong-tampermonkey-proxy) |

源文件头部原样保留：

```python
# core/api/cxsecret_font.py
##
# @Author: SocialSisterYi
# @Edit: Samueli924
# @Reference: https://github.com/SocialSisterYi/xuexiaoyi-to-xuexitong-tampermonkey-proxy
#
```

```python
# core/api/answer.py
class CacheDAO:
    """
    @Author: SocialSisterYi
    @Reference: https://github.com/SocialSisterYi/xuexiaoyi-to-xuexitong-tampermonkey-proxy
    """
```

### 2.3 题库与 AI 接口参考

| 来源 | 位置 |
|---|---|
| [DokiDoki1103/tikuAdapter](https://github.com/DokiDoki1103/tikuAdapter) | `core/api/answer.py` 的 `TikuAdapter` 实现 |
| LIKE 知识库（datam.site） | `core/api/answer.py` 的 `TikuLike` 实现 |
| [sml2h3/ddddocr](https://github.com/sml2h3/ddddocr) | 验证码 OCR 依赖 |

---

## 三、本项目的原创部分

| 项 | 内容 |
|---|---|
| 范围 | `app/` 目录全部；`README.md` / `CHANGELOG.md` / `NOTICE.md` / 各 `.bat` |
| 主要内容 | Tkinter 图形界面、线程调度、九项内核缺陷补丁、任务增强、环境自检、打包脚本 |
| 许可 | 同样以 **GPL-3.0** 发布（与上游保持一致，不拆分许可） |

`app/patches.py` 中对上游代码的九项修复，属于 GPL-3.0 允许的修改，
修改内容已在 [`CHANGELOG.md`](CHANGELOG.md) 中逐条公开说明，可对照审查。

---

## 四、整体许可

```
学习通刷课助手 图形界面版
Copyright (C) 2026  zhaofeng104 及本项目贡献者
Copyright (C) 2024-2026  Samueli924 及 chaoxing 项目贡献者
Copyright (C) 2025  skreon（core/api/captcha.py）
Copyright (C) SocialSisterYi（CacheDAO、cxsecret_font）

本程序是自由软件：你可以再分发之和/或依照由自由软件基金会发布的
GNU 通用公共许可证修改之，无论是版本 3 许可证，还是（按你的决定）
任何更新版本。

本程序是希望它能有用，但不提供任何担保；甚至没有适销性或特定用途
适用性的隐含担保。详见 GNU 通用公共许可证。

你应该随本程序收到一份 GNU 通用公共许可证的副本。
如果没有，见 <https://www.gnu.org/licenses/>。
```

完整许可证原文见根目录 [`LICENSE`](LICENSE)。

---

## 五、与官方无关

本项目与 **超星 / 学习通官方无任何关联**，未获其授权、认可或支持。
程序名中的「学习通」仅用于说明适用对象，不主张任何商标权利。
相关商标归其各自所有者。

---

## 六、联系方式

| 角色 | 联系方式 |
|---|---|
| 本项目维护者 | zhaofeng104 · 3457222856@qq.com |
| `captcha.py` 原作者 | skreon · 1340554713@qq.com |
| 上游内核作者 | Samueli924 · [GitHub](https://github.com/Samueli924/chaoxing) |

如需就**验证码模块**单独沟通，请直接联系 skreon；
如需就**内核**沟通，请走上游仓库；本仓库只接受**界面层与补丁**相关的问题。
