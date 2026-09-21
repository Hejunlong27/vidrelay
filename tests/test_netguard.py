"""网络分段校验测试（关键安全逻辑）。

这里验证的是「硬约束真的是硬的」：
国内组遇到海外 IP 必须拒绝，海外组遇到国内 IP 必须拒绝，
探测失败时必须 fail closed（拒绝），而不是放行。
"""

from __future__ import annotations

import pytest

from vidrelay import netguard
from vidrelay.netguard import NetStatus
from vidrelay.platforms import Group


@pytest.fixture(autouse=True)
def _enable_enforcement(monkeypatch):
    monkeypatch.setenv("VIDRELAY_ENFORCE_NETGUARD", "1")


class TestGroupRules:
    def test_cn_group_allows_china(self) -> None:
        v = netguard.check(Group.CN, NetStatus(ok=True, country="CN", ip_masked="1.2.3.x"))
        assert v.allowed

    def test_cn_group_rejects_oversea_ip(self) -> None:
        v = netguard.check(Group.CN, NetStatus(ok=True, country="US", ip_masked="1.2.3.x"))
        assert not v.allowed
        assert "国内直连" in v.reason
        assert "VPN" in v.reason

    def test_oversea_group_rejects_china_ip(self) -> None:
        v = netguard.check(Group.OVERSEA, NetStatus(ok=True, country="CN"))
        assert not v.allowed
        assert "海外出口 IP" in v.reason

    def test_oversea_group_allows_us(self) -> None:
        v = netguard.check(Group.OVERSEA, NetStatus(ok=True, country="US"))
        assert v.allowed

    def test_accepts_string_group(self) -> None:
        assert netguard.check("cn", NetStatus(ok=True, country="CN")).allowed


class TestFailClosed:
    def test_unknown_country_is_rejected(self) -> None:
        v = netguard.check(Group.CN, NetStatus(ok=False, message="探测失败"))
        assert not v.allowed
        assert "无法确认出口 IP 归属地" in v.reason

    def test_unknown_country_rejected_for_oversea_too(self) -> None:
        assert not netguard.check(Group.OVERSEA, NetStatus(ok=False)).allowed


class TestEnforcementSwitch:
    def test_can_be_disabled(self, monkeypatch) -> None:
        monkeypatch.setenv("VIDRELAY_ENFORCE_NETGUARD", "0")
        v = netguard.check(Group.CN, NetStatus(ok=True, country="US"))
        assert v.allowed
        assert "关闭" in v.reason

    @pytest.mark.parametrize("value", ["1", "true", "yes", ""])
    def test_default_is_enabled(self, monkeypatch, value) -> None:
        monkeypatch.setenv("VIDRELAY_ENFORCE_NETGUARD", value)
        assert netguard.enforcement_enabled()

    def test_detect_uses_first_working_source(self, monkeypatch) -> None:
        monkeypatch.setattr(netguard, "_fetch", lambda url: ("203.0.113.9", "US"))
        st = netguard.detect()
        assert st.ok and st.country == "US"
        assert st.ip_masked == "203.0.113.x"  # 完整 IP 不进日志

    def test_detect_falls_back(self, monkeypatch) -> None:
        calls: list[str] = []

        def fake(url: str):
            calls.append(url)
            return (None, "JP") if len(calls) == 2 else (None, None)

        monkeypatch.setattr(netguard, "_fetch", fake)
        st = netguard.detect()
        assert st.ok and st.country == "JP"
        assert len(calls) == 2

    def test_detect_reports_failure(self, monkeypatch) -> None:
        monkeypatch.setattr(netguard, "_fetch", lambda url: (None, None))
        assert not netguard.detect().ok


class TestMasking:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("1.2.3.4", "1.2.3.x"),
            ("2001:db8::1", "2001:db8::x"),
            (None, None),
            ("weird", "x"),
        ],
    )
    def test_mask(self, raw, expected) -> None:
        assert netguard._mask_ip(raw) == expected
