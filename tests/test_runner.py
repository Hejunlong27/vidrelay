"""编排层测试：分组互斥、失败隔离、结果落地。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vidrelay import runner
from vidrelay.adapters import PublishRequest, available_platforms, build, missing_platforms
from vidrelay.contract import PublishResult
from vidrelay.platforms import Group, resolve
from vidrelay.runner import OrchestrationError, RunOptions, run

FIXTURES = Path(__file__).parent / "fixtures"
VALID = FIXTURES / "valid.yaml"
INVALID = FIXTURES / "invalid.yaml"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """默认关掉网络探测，让测试可离线运行。"""
    monkeypatch.setenv("VIDRELAY_ENFORCE_NETGUARD", "0")


def _opts(**kw) -> RunOptions:
    base = dict(meta_path=str(VALID), save_report=False)
    base.update(kw)
    return RunOptions(**base)


class TestGroupExclusivity:
    def test_mixed_groups_are_rejected(self) -> None:
        with pytest.raises(OrchestrationError) as exc:
            run(_opts())
        msg = str(exc.value)
        assert "互斥" in msg
        assert "--group cn" in msg
        assert "--group oversea" in msg

    def test_single_group_runs(self) -> None:
        report = run(_opts(group=Group.OVERSEA))
        assert {r.platform for r in report.results} == {"tiktok", "youtube", "dailymotion"}

    def test_cn_group_runs(self) -> None:
        report = run(_opts(group=Group.CN))
        assert {r.platform for r in report.results} == {"douyin", "kuaishou", "bilibili"}

    def test_group_without_platforms_errors(self) -> None:
        with pytest.raises(OrchestrationError, match="没有任何已配置的平台"):
            run(_opts(only=["youtube"], group=Group.CN))


class TestPlatformSelection:
    def test_only_platform_absent_from_meta_errors(self) -> None:
        with pytest.raises(OrchestrationError, match="meta 文件里没有"):
            run(
                RunOptions(
                    meta_path=str(FIXTURES / "oversea_only.yaml"),
                    only=["douyin"],
                    save_report=False,
                )
            )

    def test_only_unknown_platform_errors(self) -> None:
        with pytest.raises(KeyError, match="未知平台"):
            run(_opts(group=Group.OVERSEA, only=["weibo"]))

    def test_only_selects_single_platform(self) -> None:
        report = run(_opts(group=Group.OVERSEA, only=["youtube"]))
        assert [r.platform for r in report.results] == ["youtube"]


class TestInvalidMeta:
    def test_validation_error_is_wrapped(self) -> None:
        with pytest.raises(OrchestrationError) as exc:
            run(RunOptions(meta_path=str(INVALID), save_report=False))
        assert "platforms.youtube.title" in str(exc.value)


class TestFailureIsolation:
    def test_unimplemented_adapters_fail_but_others_still_run(self) -> None:
        """没有实现适配器的平台要明确失败，且不影响其他平台。"""
        report = run(_opts(group=Group.OVERSEA))
        assert len(report.results) == 3
        assert not report.all_ok
        assert all("尚未实现" in (r.error or "") for r in report.failed)

    def test_results_are_sorted_for_stable_diff(self) -> None:
        report = run(_opts(group=Group.OVERSEA))
        assert [r.platform for r in report.results] == sorted(r.platform for r in report.results)

    def test_dry_run_propagates(self) -> None:
        report = run(_opts(group=Group.OVERSEA, dry_run=True))
        assert all(r.dry_run for r in report.results)

    def test_one_adapter_raising_does_not_kill_the_run(self, monkeypatch) -> None:
        real_build = runner.build

        class Boom:
            platform_key = "youtube"

            def __init__(self, request):
                self.request = request

            def preflight(self):
                return []

            def publish(self):
                raise RuntimeError("引擎炸了")

        def fake_build(req):
            return Boom(req) if req.platform.key == "youtube" else real_build(req)

        monkeypatch.setattr(runner, "build", fake_build)
        report = run(_opts(group=Group.OVERSEA))
        boom = next(r for r in report.results if r.platform == "youtube")
        assert not boom.ok and "RuntimeError" in (boom.error or "")
        # 其他平台仍产出结果
        assert {r.platform for r in report.results} == {"tiktok", "youtube", "dailymotion"}


class TestReportPersistence:
    def test_saves_report_json(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setattr(runner, "repo_root", lambda: tmp_path)
        report = run(_opts(group=Group.OVERSEA, save_report=True))

        files = list((tmp_path / "runs").glob("*.json"))
        assert len(files) == 1

        payload = json.loads(files[0].read_text(encoding="utf-8"))
        assert payload["contract_version"] == report.contract_version
        assert payload["group"] == "oversea"
        assert payload["summary"]["total"] == 3
        assert len(payload["results"]) == 3

    def test_no_save_when_disabled(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setattr(runner, "repo_root", lambda: tmp_path)
        run(_opts(group=Group.OVERSEA, save_report=False))
        assert not (tmp_path / "runs").exists()


class TestAdapterRegistry:
    def test_all_platforms_declared_but_not_yet_implemented(self) -> None:
        assert available_platforms() == []
        assert set(missing_platforms()) == {
            "douyin",
            "kuaishou",
            "bilibili",
            "tiktok",
            "youtube",
            "dailymotion",
        }

    def test_unknown_platform_raises(self) -> None:
        with pytest.raises(KeyError, match="未知平台"):
            resolve("weibo")

    def test_not_implemented_adapter_names_the_engine(self) -> None:
        req = PublishRequest(platform=resolve("youtube"), video="v.mp4", title="t")
        result = build(req).publish()
        assert isinstance(result, PublishResult)
        assert not result.ok
        assert "YouTube Data API v3" in (result.error or "")
