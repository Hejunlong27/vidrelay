"""契约测试（M0.2 验收）。"""

from __future__ import annotations

import pytest

from vidrelay.contract import CONTRACT_VERSION, PublishReport, PublishResult


class TestPublishResult:
    def test_success_roundtrip(self) -> None:
        r = PublishResult.success(
            "youtube",
            video_id="dQw4w9WgXcQ",
            url="https://youtu.be/dQw4w9WgXcQ",
            duration_ms=42130,
            custom_field="x",
        )
        assert r.ok and r.error is None
        assert r.extra == {"custom_field": "x"}

        back = PublishResult.from_dict(r.to_dict())
        assert back == r

    def test_failure_roundtrip(self) -> None:
        r = PublishResult.failure("douyin", "Cookie 已过期", duration_ms=800)
        assert not r.ok and r.error == "Cookie 已过期"
        assert PublishResult.from_dict(r.to_dict()) == r

    def test_dict_key_order_is_stable(self) -> None:
        r = PublishResult.success("bilibili", video_id="BV1xx")
        assert list(r.to_dict()) == [
            "platform",
            "ok",
            "video_id",
            "url",
            "scheduled_at",
            "duration_ms",
            "error",
            "dry_run",
        ]

    def test_ok_with_error_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="ok=True 时不应携带 error"):
            PublishResult(platform="youtube", ok=True, error="矛盾")

    def test_failure_without_error_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="必须给出 error"):
            PublishResult(platform="youtube", ok=False)

    def test_negative_duration_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="不能为负"):
            PublishResult(platform="youtube", ok=True, duration_ms=-1)

    def test_unknown_field_on_load_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="未知字段"):
            PublishResult.from_dict({"platform": "youtube", "ok": True, "surprise": 1})

    def test_extra_omitted_when_empty(self) -> None:
        assert "extra" not in PublishResult.success("youtube").to_dict()

    def test_short_render(self) -> None:
        assert "dry-run" in PublishResult.success("youtube", url="u", dry_run=True).short()
        assert PublishResult.failure("douyin", "boom").short().startswith("✗ douyin")


class TestPublishReport:
    def test_empty_report_is_not_ok(self) -> None:
        # 关键：什么都没跑，不能被当成成功
        assert PublishReport().all_ok is False

    def test_aggregation(self) -> None:
        rep = PublishReport()
        rep.add(PublishResult.success("youtube", url="a"))
        rep.add(PublishResult.success("dailymotion", url="b"))
        rep.add(PublishResult.failure("douyin", "cookie 失效"))
        rep.close()

        assert not rep.all_ok
        assert len(rep.succeeded) == 2
        assert [r.platform for r in rep.failed] == ["douyin"]

        d = rep.to_dict()
        assert d["contract_version"] == CONTRACT_VERSION
        assert d["summary"] == {
            "total": 3,
            "ok": 2,
            "failed": 1,
            "failed_platforms": ["douyin"],
        }
        assert d["finished_at"] is not None

    def test_all_ok_when_everything_succeeds(self) -> None:
        rep = PublishReport()
        rep.add(PublishResult.success("youtube", url="a"))
        assert rep.all_ok

    def test_render_mentions_failed_platforms(self) -> None:
        rep = PublishReport()
        rep.add(PublishResult.success("youtube", url="a"))
        rep.add(PublishResult.failure("douyin", "boom"))
        text = rep.render()
        assert "1/2 成功" in text
        assert "douyin" in text
