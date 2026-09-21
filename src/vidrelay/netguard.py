"""出口 IP 归属校验（网络分段硬约束）。

**为什么这是一个代码模块，而不是文档里的一句注意事项：**

国内平台与海外平台对出口 IP 的要求是互斥的——
国内平台必须国内直连（抖音风控会把「伪装异地登录」判定为异常行为），
海外平台需要海外出口 IP。靠人记着「跑之前先关 VPN」迟早会出事，
所以把它做成断言：不符合就直接拒绝执行。

设计取向是 **fail closed（失败即拒绝）**：
查不到归属地时，宁可拒绝执行，也不放行一次可能违规的发布。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

from vidrelay.platforms import Group

DEFAULT_LOOKUP_URL = "https://ipinfo.io/json"
FALLBACK_LOOKUP_URL = "https://api.country.is/"
TIMEOUT_SECONDS = 8

CN_COUNTRY_CODES = {"CN"}


@dataclass
class NetStatus:
    """一次出口网络探测的结果。"""

    ok: bool
    country: str | None = None
    ip_masked: str | None = None
    source: str | None = None
    message: str = ""

    def render(self) -> str:
        where = self.country or "未知"
        ip = self.ip_masked or "未知"
        return f"出口 IP {ip} · 归属地 {where} · {self.message}"


def _mask_ip(ip: str | None) -> str | None:
    """只保留前三段，避免把完整公网 IP 写进日志。"""
    if not ip:
        return None
    if ":" in ip:  # IPv6：只保留前两组
        groups = [g for g in ip.split(":") if g][:2]
        return ":".join(groups) + "::x" if groups else None
    parts = ip.split(".")
    if len(parts) == 4:
        return ".".join(parts[:3]) + ".x"
    return "x"


def _fetch(url: str) -> tuple[str | None, str | None]:
    """返回 (ip, country)。任何异常都吞掉并返回 (None, None)。"""
    req = urllib.request.Request(url, headers={"User-Agent": "vidrelay-netguard/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError):
        return None, None

    if not isinstance(payload, dict):
        return None, None
    ip = payload.get("ip") or payload.get("IP")
    country = payload.get("country") or payload.get("country_code")
    if isinstance(country, str):
        country = country.strip().upper()
    return (str(ip) if ip else None), (str(country) if country else None)


def detect() -> NetStatus:
    """探测当前出口 IP 的归属国家。"""
    primary = os.environ.get("VIDRELAY_IP_LOOKUP_URL") or DEFAULT_LOOKUP_URL
    for url in (primary, FALLBACK_LOOKUP_URL):
        if not url:
            continue
        ip, country = _fetch(url)
        if country:
            return NetStatus(
                ok=True,
                country=country,
                ip_masked=_mask_ip(ip),
                source=url,
                message="探测成功",
            )
    return NetStatus(
        ok=False,
        message="无法确定出口 IP 归属地（网络不可达或被拦截）",
    )


def enforcement_enabled() -> bool:
    return (os.environ.get("VIDRELAY_ENFORCE_NETGUARD") or "1").strip() not in {"0", "false", "no"}


@dataclass
class GuardVerdict:
    allowed: bool
    status: NetStatus
    reason: str = ""


def check(group: Group | str, status: NetStatus | None = None) -> GuardVerdict:
    """判断当前出口网络是否允许执行该分组的发布。"""
    g = Group(group) if not isinstance(group, Group) else group
    st = status or detect()

    if not enforcement_enabled():
        return GuardVerdict(True, st, "校验已被 VIDRELAY_ENFORCE_NETGUARD=0 关闭")

    if not st.ok:
        # fail closed：查不到就拒绝
        return GuardVerdict(
            False,
            st,
            "无法确认出口 IP 归属地，出于安全考虑拒绝执行。"
            "确认网络正常后可设 VIDRELAY_ENFORCE_NETGUARD=0 跳过（不建议）",
        )

    is_cn = st.country in CN_COUNTRY_CODES

    if g is Group.CN:
        if is_cn:
            return GuardVerdict(True, st, "国内组：出口 IP 在中国大陆，符合要求")
        return GuardVerdict(
            False,
            st,
            f"国内组要求国内直连，当前出口 IP 归属地是 {st.country}。"
            "请断开 VPN / 代理后重试。用海外 IP 发布国内平台会被风控判定为伪装异地登录",
        )

    if is_cn:
        return GuardVerdict(
            False,
            st,
            "海外组需要海外出口 IP，当前出口 IP 归属地是中国大陆。请先连上海外节点",
        )
    return GuardVerdict(True, st, f"海外组：出口 IP 归属地 {st.country}，符合要求")
