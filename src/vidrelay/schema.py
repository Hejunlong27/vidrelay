"""meta.yaml 的校验器（M0.4）。

校验的目标不是「拦住错误」，而是**让人一眼知道错在哪一行、哪个字段**。
所以每个问题都带字段路径 + 原始文件行号。

行号是这么拿到的：PyYAML 解析后只剩纯数据，丢失了位置信息；
这里额外扫描一遍源文本，用缩进栈还原出「字段路径 → 行号」的映射。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from vidrelay.platforms import PLATFORMS

# 每个平台允许出现的字段： 字段名 -> (类型, 是否必填)
_ALL = {
    "title": ("str", True),
    "desc": ("str", False),
    "tags": ("list", False),
    "cover": ("str", False),
    "schedule": ("bool", False),
    "extra": ("dict", False),
}

PLATFORM_FIELDS: dict[str, dict[str, tuple[str, bool]]] = {
    "douyin": {**_ALL, "product_link": ("str", False)},
    "kuaishou": dict(_ALL),
    "bilibili": {**_ALL, "tid": ("int", True), "copyright": ("int", False)},
    "tiktok": {**_ALL, "privacy": ("enum:public,unlisted,private", False)},
    "youtube": {
        **_ALL,
        "category": ("int", False),
        "privacy": ("enum:public,unlisted,private", False),
        "playlist": ("str", False),
        "short": ("bool", False),
    },
    "dailymotion": {**_ALL, "channel": ("str", False), "privacy": ("enum:public,private", False)},
}

TOP_FIELDS = {"video", "cover", "common", "platforms"}
COMMON_FIELDS = {"publish_at", "timezone", "account"}

_ISO_WITH_TZ = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?([+-]\d{2}:?\d{2}|Z)?$")


@dataclass
class Issue:
    """一条校验问题。"""

    path: str
    message: str
    line: int | None = None
    severity: str = "error"  # error | warning

    def render(self, source: str | None = None) -> str:
        loc = f"{source}:{self.line}" if (source and self.line) else (source or "<meta>")
        mark = "错误" if self.severity == "error" else "警告"
        return f"[{mark}] {loc}  字段 `{self.path}` — {self.message}"


@dataclass
class ValidationResult:
    issues: list[Issue] = field(default_factory=list)
    data: dict[str, Any] | None = None

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors


class MetaValidationError(Exception):
    """校验未通过。异常信息里已经排好序、带行号。"""

    def __init__(self, result: ValidationResult, source: str | None = None) -> None:
        self.result = result
        body = "\n".join(i.render(source) for i in result.issues)
        super().__init__(f"meta 校验未通过：\n{body}")


# ---------------------------------------------------------------- 行号定位


def build_line_index(text: str) -> dict[str, int]:
    """扫描 YAML 源文本，返回 {字段路径: 行号}（行号从 1 开始）。

    只做路径定位，不做语义解析——语义交给 PyYAML。
    """
    index: dict[str, int] = {}
    stack: list[tuple[int, str]] = []  # (缩进, 键名)
    list_counters: dict[str, int] = {}

    for lineno, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        stripped = raw.strip()
        indent = len(raw) - len(raw.lstrip())

        if stripped.startswith("- "):
            parent = ".".join(k for _, k in stack)
            n = list_counters.get(parent, 0)
            list_counters[parent] = n + 1
            index.setdefault(f"{parent}[{n}]", lineno)
            continue

        m = re.match(r"^([^\s:#][^:]*):", stripped)
        if not m:
            continue
        key = m.group(1).strip().strip('"\'')

        while stack and stack[-1][0] >= indent:
            stack.pop()
        stack.append((indent, key))
        path = ".".join(k for _, k in stack)
        index.setdefault(path, lineno)

    return index


def _lookup(index: dict[str, int], path: str) -> int | None:
    if path in index:
        return index[path]
    # 逐级向上回退：platforms.youtube.title -> platforms.youtube
    parts = path.split(".")
    while parts:
        parts.pop()
        if ".".join(parts) in index:
            return index[".".join(parts)]
    return None


# ---------------------------------------------------------------- 类型判断


def _type_ok(value: Any, spec: str) -> bool:
    if spec == "str":
        return isinstance(value, str)
    if spec == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if spec == "bool":
        return isinstance(value, bool)
    if spec == "list":
        return isinstance(value, list)
    if spec == "dict":
        return isinstance(value, dict)
    if spec.startswith("enum:"):
        return isinstance(value, str) and value in spec[5:].split(",")
    return True


def _type_hint(spec: str) -> str:
    if spec.startswith("enum:"):
        return "取值必须是 " + " / ".join(spec[5:].split(",")) + " 之一"
    return {"str": "字符串", "int": "整数", "bool": "布尔值", "list": "列表", "dict": "字典"}.get(
        spec, spec
    )


# ---------------------------------------------------------------- 主校验


def validate_file(path: str | Path) -> ValidationResult:
    p = Path(path)
    if not p.is_file():
        r = ValidationResult()
        r.issues.append(Issue(path="<file>", message=f"文件不存在：{p}"))
        return r
    return validate_text(p.read_text(encoding="utf-8"), source=str(p))


def validate_text(text: str, source: str | None = None) -> ValidationResult:
    result = ValidationResult()
    index = build_line_index(text)

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        result.issues.append(
            Issue(
                path="<yaml>",
                message=f"YAML 语法错误：{getattr(exc, 'problem', exc)}",
                line=(mark.line + 1) if mark else None,
            )
        )
        return result

    if data is None:
        result.issues.append(Issue(path="<root>", message="文件是空的"))
        return result
    if not isinstance(data, dict):
        result.issues.append(Issue(path="<root>", message="顶层必须是一个映射（key: value）"))
        return result

    _check_top(data, index, result)
    _check_common(data, index, result)
    _check_platforms(data, index, result)

    result.data = data
    _ = source  # 保留参数以便未来在 Issue 上直接带 source
    return result


def _check_top(data: dict, index: dict[str, int], result: ValidationResult) -> None:
    for key in data:
        if key not in TOP_FIELDS:
            result.issues.append(
                Issue(
                    path=str(key),
                    message=f"未知顶层字段。允许的是：{', '.join(sorted(TOP_FIELDS))}",
                    line=_lookup(index, str(key)),
                )
            )

    video = data.get("video")
    if video is None:
        result.issues.append(
            Issue(path="video", message="必填，指向视频文件路径", line=_lookup(index, "video"))
        )
    elif not isinstance(video, str) or not video.strip():
        result.issues.append(
            Issue(path="video", message="必须是非空字符串", line=_lookup(index, "video"))
        )


def _check_common(data: dict, index: dict[str, int], result: ValidationResult) -> None:
    common = data.get("common")
    if common is None:
        return
    if not isinstance(common, dict):
        result.issues.append(
            Issue(path="common", message="必须是映射", line=_lookup(index, "common"))
        )
        return

    for key in common:
        if key not in COMMON_FIELDS:
            result.issues.append(
                Issue(
                    path=f"common.{key}",
                    message=f"未知字段。允许的是：{', '.join(sorted(COMMON_FIELDS))}",
                    line=_lookup(index, f"common.{key}"),
                )
            )

    publish_at = common.get("publish_at")
    if publish_at is None:
        return
    if not isinstance(publish_at, str) or not _ISO_WITH_TZ.match(publish_at.strip()):
        result.issues.append(
            Issue(
                path="common.publish_at",
                message="需为 ISO 8601 时间，例如 2026-09-22T09:30+08:00",
                line=_lookup(index, "common.publish_at"),
            )
        )
    elif not publish_at.strip().endswith("Z") and "+" not in publish_at and "-" not in publish_at[10:]:
        result.issues.append(
            Issue(
                path="common.publish_at",
                message="缺少时区信息。请写偏移量（+08:00）或 Z，不要留裸本地时间",
                line=_lookup(index, "common.publish_at"),
                severity="warning",
            )
        )
    else:
        try:
            datetime.fromisoformat(publish_at.replace("Z", "+00:00"))
        except ValueError:
            result.issues.append(
                Issue(
                    path="common.publish_at",
                    message="不是合法的时间值",
                    line=_lookup(index, "common.publish_at"),
                )
            )


def _check_platforms(data: dict, index: dict[str, int], result: ValidationResult) -> None:
    platforms = data.get("platforms")
    if platforms is None:
        result.issues.append(
            Issue(
                path="platforms",
                message="必填，至少要有一个平台",
                line=_lookup(index, "platforms"),
            )
        )
        return
    if not isinstance(platforms, dict) or not platforms:
        result.issues.append(
            Issue(
                path="platforms",
                message="必须是非空映射，键为平台名",
                line=_lookup(index, "platforms"),
            )
        )
        return

    for name, cfg in platforms.items():
        base = f"platforms.{name}"
        if name not in PLATFORMS:
            result.issues.append(
                Issue(
                    path=base,
                    message=f"未知平台。可用：{', '.join(sorted(PLATFORMS))}",
                    line=_lookup(index, base),
                )
            )
            continue
        if cfg is None:
            result.issues.append(
                Issue(path=base, message="该平台下没有任何字段", line=_lookup(index, base))
            )
            continue
        if not isinstance(cfg, dict):
            result.issues.append(
                Issue(path=base, message="必须是映射（title: ... 形式）", line=_lookup(index, base))
            )
            continue

        spec = PLATFORM_FIELDS[name]

        # 必填字段
        for field_name, (type_spec, required) in spec.items():
            path = f"{base}.{field_name}"
            if field_name not in cfg or cfg[field_name] is None:
                if required:
                    result.issues.append(
                        Issue(
                            path=path,
                            message=f"必填字段缺失（{_type_hint(type_spec)}）",
                            line=_lookup(index, path),
                        )
                    )
                continue
            value = cfg[field_name]
            if not _type_ok(value, type_spec):
                result.issues.append(
                    Issue(
                        path=path,
                        message=f"类型不对，{_type_hint(type_spec)}；实际是 {type(value).__name__}",
                        line=_lookup(index, path),
                    )
                )
                continue
            if type_spec == "list" and not all(isinstance(v, str) for v in value):
                result.issues.append(
                    Issue(path=path, message="列表中每一项都必须是字符串", line=_lookup(index, path))
                )

        # 未知字段
        for field_name in cfg:
            if field_name not in spec:
                result.issues.append(
                    Issue(
                        path=f"{base}.{field_name}",
                        message=f"{PLATFORMS[name].display_name} 不支持的字段。允许的是："
                        f"{', '.join(sorted(spec))}",
                        line=_lookup(index, f"{base}.{field_name}"),
                    )
                )

        # 平台特有提醒
        if name == "youtube" and cfg.get("short") and cfg.get("title"):
            if "#shorts" not in str(cfg["title"]).lower():
                result.issues.append(
                    Issue(
                        path=f"{base}.title",
                        message="标记为 Short 时建议标题带 #Shorts，便于平台归类",
                        line=_lookup(index, f"{base}.title"),
                        severity="warning",
                    )
                )
        if name == "dailymotion" and cfg.get("title"):
            if re.search(r"[\u4e00-\u9fff]", str(cfg["title"])):
                result.issues.append(
                    Issue(
                        path=f"{base}.title",
                        message="Dailymotion 面向欧美受众，标题含中文可能影响分发",
                        line=_lookup(index, f"{base}.title"),
                        severity="warning",
                    )
                )


def load_and_validate(path: str | Path) -> dict[str, Any]:
    """校验通过则返回数据，否则抛 MetaValidationError。"""
    result = validate_file(path)
    if not result.ok:
        raise MetaValidationError(result, source=str(path))
    return result.data or {}
