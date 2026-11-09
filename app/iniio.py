# -*- coding: utf-8 -*-
"""config.ini 读写：基于模板做定向替换，完整保留上游的中文注释。"""

from __future__ import annotations

import os
import re
from typing import Any

SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")
KEY_RE = re.compile(r"^(\s*)([A-Za-z0-9_]+)(\s*)=\s*(.*)$")


def _escape(value: str) -> str:
    """ConfigParser 默认开 BasicInterpolation，值里的 % 必须写成 %%。"""
    return str(value).replace("%", "%%")


def _unescape(value: str) -> str:
    return value.replace("%%", "%")


def read_values(text: str) -> dict[str, dict[str, str]]:
    """把 ini 文本解析成 {section: {key: value}}，忽略注释与空行。"""
    out: dict[str, dict[str, str]] = {}
    section = ""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped[0] in ";#":
            continue
        m = SECTION_RE.match(line)
        if m:
            section = m.group(1).strip()
            out.setdefault(section, {})
            continue
        m = KEY_RE.match(line)
        if m and section:
            out[section][m.group(2)] = _unescape(m.group(4).strip())
    return out


def render(template: str, values: dict[str, dict[str, Any]]) -> str:
    """按模板逐行改写值；模板里没有的键追加到所属 section 末尾。"""
    lines = template.splitlines()
    seen: dict[str, set[str]] = {}
    section = ""
    result: list[str] = []

    for line in lines:
        m = SECTION_RE.match(line)
        if m:
            section = m.group(1).strip()
            result.append(line)
            continue

        m = KEY_RE.match(line)
        if m and section:
            key = m.group(2)
            if section in values and key in values[section]:
                value = _escape(values[section][key])
                result.append(f"{m.group(1)}{key}{m.group(3)}={value}")
                seen.setdefault(section, set()).add(key)
                continue
            result.append(line)
            continue

        result.append(line)

    # 模板里缺失但用户配了的键，补在对应 section 结尾
    text = "\n".join(result)
    for sec, kv in values.items():
        missing = {k: v for k, v in kv.items() if k not in seen.get(sec, set())}
        if not missing:
            continue
        block = "\n".join(f"{k}={_escape(v)}" for k, v in missing.items())
        pattern = re.compile(r"(^\s*\[" + re.escape(sec) + r"\]\s*$)", re.MULTILINE)
        if pattern.search(text):
            section_end = _find_section_end(text, sec)
            text = text[:section_end] + block + "\n" + text[section_end:]
        else:
            text = text.rstrip("\n") + f"\n\n[{sec}]\n{block}\n"

    return text.rstrip("\n") + "\n"


def _find_section_end(text: str, section: str) -> int:
    """返回该 section 最后一个非空内容行之后的插入位置。"""
    lines = text.splitlines(keepends=True)
    inside = False
    last_content = 0
    offset = 0
    for line in lines:
        if SECTION_RE.match(line):
            if inside:
                break
            inside = SECTION_RE.match(line).group(1).strip() == section
            offset += len(line)
            if inside:
                last_content = offset
            continue
        if inside:
            offset += len(line)
            if line.strip():
                last_content = offset
    return last_content


def load(path: str, template_path: str) -> dict[str, dict[str, str]]:
    """读取用户配置；配置不存在时返回模板默认值。"""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return read_values(f.read())
    with open(template_path, "r", encoding="utf-8", errors="replace") as f:
        return read_values(f.read())


def save(path: str, template_path: str, values: dict[str, dict[str, Any]]) -> None:
    """写回配置，保留模板注释。写 UTF-8 无 BOM，避免 ConfigParser 读到 \\ufeff。"""
    with open(template_path, "r", encoding="utf-8", errors="replace") as f:
        template = f.read()
    text = render(template, values)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
