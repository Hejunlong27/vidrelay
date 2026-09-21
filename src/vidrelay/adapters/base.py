"""适配器层（防腐层）的基类与注册表。

**这一层的存在意义：** 第三方引擎会停更、会被替换。
只要适配器对外暴露的契约不变，底层从 social-auto-upload 换成别的、
从浏览器自动化换成官方 API，上层编排和 Agent 侧都不需要改动。

新增一个平台 = 新增一个文件 + 一个装饰器。不需要改 CLI，也不需要改编排。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Type

from vidrelay.contract import PublishResult
from vidrelay.platforms import PLATFORMS, Platform, resolve


@dataclass
class PublishRequest:
    """交给适配器的、已经解析好的单平台请求。"""

    platform: Platform
    video: str
    title: str
    desc: str = ""
    tags: list[str] = field(default_factory=list)
    cover: str | None = None
    publish_at: str | None = None
    dry_run: bool = False
    account: str | None = None
    options: dict[str, Any] = field(default_factory=dict)
    """平台特有字段（如 B站 tid、YouTube category）。"""


class Adapter(ABC):
    """所有平台适配器的基类。"""

    platform_key: str = ""

    def __init__(self, request: PublishRequest) -> None:
        self.request = request

    @property
    def platform(self) -> Platform:
        return self.request.platform

    @abstractmethod
    def publish(self) -> PublishResult:
        """执行发布。**必须**返回 PublishResult，异常不要往外抛。"""
        raise NotImplementedError

    def preflight(self) -> list[str]:
        """发布前检查。返回问题列表，空列表表示可以继续。"""
        return []


_REGISTRY: dict[str, Type[Adapter]] = {}


def register(cls: Type[Adapter]) -> Type[Adapter]:
    """注册一个适配器。用平台 key 作为唯一标识。"""
    key = cls.platform_key
    if not key:
        raise ValueError(f"{cls.__name__} 必须设置 platform_key")
    if key not in PLATFORMS:
        raise ValueError(f"{cls.__name__} 的 platform_key={key!r} 不在平台注册表中")
    if key in _REGISTRY and _REGISTRY[key] is not cls:
        raise ValueError(f"平台 {key} 已经有适配器：{_REGISTRY[key].__name__}")
    _REGISTRY[key] = cls
    return cls


def get_adapter_class(key: str) -> Type[Adapter] | None:
    resolve(key)  # 顺带校验 key 合法性
    return _REGISTRY.get(key)


def available_platforms() -> list[str]:
    return sorted(_REGISTRY)


def missing_platforms() -> list[str]:
    return sorted(set(PLATFORMS) - set(_REGISTRY))


def build(request: PublishRequest) -> Adapter:
    """按平台构造适配器实例。没有实现时返回一个「未实现」占位。"""
    cls = _REGISTRY.get(request.platform.key)
    if cls is None:
        return NotImplementedAdapter(request)
    return cls(request)


class NotImplementedAdapter(Adapter):
    """占位适配器：明确报「还没做」，而不是静默跳过。

    静默跳过会让「6 个平台只发了 2 个」这种事悄悄发生。
    """

    def __init__(self, request: PublishRequest) -> None:
        super().__init__(request)
        self.platform_key = request.platform.key

    def publish(self) -> PublishResult:
        return PublishResult.failure(
            self.request.platform.key,
            f"适配器尚未实现（{self.request.platform.display_name}）。"
            f"计划引擎：{self.request.platform.engine}",
            dry_run=self.request.dry_run,
        )
