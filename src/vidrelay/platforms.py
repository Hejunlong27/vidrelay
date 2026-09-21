"""平台注册表。

这里定义「有哪些平台」以及「它们属于哪一组」。

为什么分组是硬性的：国内平台与海外平台**不可能在同一次网络会话里完成发布**。
国内平台必须国内直连、完全断开 VPN；海外平台需要海外出口 IP。
把分组写进数据结构，是为了让编排层能据此做网络校验（见 netguard.py）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Group(str, Enum):
    """发布分组。网络要求互斥，必须分开执行。"""

    CN = "cn"
    OVERSEA = "oversea"

    def __str__(self) -> str:  # pragma: no cover - 便于日志
        return self.value


@dataclass(frozen=True)
class Platform:
    """一个目标平台的静态描述。"""

    key: str
    display_name: str
    group: Group
    engine: str
    """底层执行引擎。适配器是防腐层，引擎可以被替换。"""

    needs_vpn_off: bool
    """执行前是否要求断开 VPN。国内平台为 True。"""

    official_api: bool
    """是否有官方开放的上传 API。"""

    access: str
    """接入方式的人类可读标签，用于 CLI 展示。"""


PLATFORMS: dict[str, Platform] = {
    p.key: p
    for p in (
        # ---------- 国内组：必须国内直连，禁 VPN ----------
        Platform(
            key="douyin",
            display_name="抖音",
            group=Group.CN,
            engine="social-auto-upload (Playwright + Cookie)",
            needs_vpn_off=True,
            official_api=False,
            access="浏览器自动化",
        ),
        Platform(
            key="kuaishou",
            display_name="快手",
            group=Group.CN,
            engine="social-auto-upload (Playwright + Cookie)",
            needs_vpn_off=True,
            official_api=False,
            access="浏览器自动化",
        ),
        Platform(
            key="bilibili",
            display_name="B站",
            group=Group.CN,
            engine="biliup (命令行投稿)",
            needs_vpn_off=True,
            official_api=False,
            access="命令行",
        ),
        # ---------- 海外组：需要海外出口 IP ----------
        Platform(
            key="tiktok",
            display_name="TikTok",
            group=Group.OVERSEA,
            engine="Content Posting API / Playwright 兜底",
            needs_vpn_off=False,
            official_api=True,
            access="官方 API",
        ),
        Platform(
            key="youtube",
            display_name="YouTube",
            group=Group.OVERSEA,
            engine="YouTube Data API v3",
            needs_vpn_off=False,
            official_api=True,
            access="官方 API",
        ),
        Platform(
            key="dailymotion",
            display_name="Dailymotion",
            group=Group.OVERSEA,
            engine="Dailymotion Graph API",
            needs_vpn_off=False,
            official_api=True,
            access="官方 API",
        ),
    )
}


def platforms_in(group: Group | str) -> list[Platform]:
    """返回某一组下的全部平台。"""
    g = Group(group) if not isinstance(group, Group) else group
    return [p for p in PLATFORMS.values() if p.group is g]


def resolve(key: str) -> Platform:
    """按 key 取平台，取不到就明确报错（不要静默忽略）。"""
    try:
        return PLATFORMS[key]
    except KeyError:
        known = ", ".join(sorted(PLATFORMS))
        raise KeyError(f"未知平台 {key!r}。可用平台：{known}") from None
