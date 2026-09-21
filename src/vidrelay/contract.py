"""统一返回契约。

**这是整个项目最不能省的一个文件。**

所有平台适配器——不管底层是官方 API 还是浏览器自动化——都必须返回同一个结构。
编排层因此不需要为任何平台写特例；Agent 每次发布只需要读回一份汇总 JSON，
token 消耗被压到 1–3K，而不是逐页读快照的 50–150K。

契约一旦定下就不要随意改字段名：改契约等于同时改 6 个适配器。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

CONTRACT_VERSION = "1.0"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class PublishResult:
    """单个平台一次发布的执行结果。

    成功与失败使用**同一个结构**，用 ``ok`` 区分。
    这样调用方不需要写两套解析逻辑，失败信息也不会被丢掉。
    """

    platform: str
    ok: bool
    video_id: str | None = None
    """平台侧的视频 ID。dry-run 时为 None。"""
    url: str | None = None
    """可直接打开的链接。"""
    scheduled_at: str | None = None
    """定时发布时间（ISO 8601，含时区）。立即发布时为 None。"""
    duration_ms: int | None = None
    """该平台本次发布耗时（毫秒）。"""
    error: str | None = None
    """失败原因。ok=True 时必须为 None。"""
    dry_run: bool = False
    """是否为预演。预演不得产生任何真实发布。"""
    extra: dict[str, Any] = field(default_factory=dict)
    """平台特有的补充信息（如 B站 的 bvid、TikTok 的 publish_id）。"""

    def __post_init__(self) -> None:
        if self.ok and self.error:
            raise ValueError(f"{self.platform}: ok=True 时不应携带 error")
        if not self.ok and not self.error:
            raise ValueError(f"{self.platform}: ok=False 时必须给出 error")
        if self.duration_ms is not None and self.duration_ms < 0:
            raise ValueError(f"{self.platform}: duration_ms 不能为负")

    # ---------- 构造快捷方式 ----------

    @classmethod
    def success(
        cls,
        platform: str,
        video_id: str | None = None,
        url: str | None = None,
        scheduled_at: str | None = None,
        duration_ms: int | None = None,
        dry_run: bool = False,
        **extra: Any,
    ) -> "PublishResult":
        return cls(
            platform=platform,
            ok=True,
            video_id=video_id,
            url=url,
            scheduled_at=scheduled_at,
            duration_ms=duration_ms,
            dry_run=dry_run,
            extra=extra,
        )

    @classmethod
    def failure(
        cls,
        platform: str,
        error: str,
        duration_ms: int | None = None,
        dry_run: bool = False,
        **extra: Any,
    ) -> "PublishResult":
        return cls(
            platform=platform,
            ok=False,
            error=error,
            duration_ms=duration_ms,
            dry_run=dry_run,
            extra=extra,
        )

    # ---------- 序列化 ----------

    def to_dict(self) -> dict[str, Any]:
        """转成稳定的字典结构。键顺序固定，便于人读与 diff。"""
        out: dict[str, Any] = {
            "platform": self.platform,
            "ok": self.ok,
            "video_id": self.video_id,
            "url": self.url,
            "scheduled_at": self.scheduled_at,
            "duration_ms": self.duration_ms,
            "error": self.error,
            "dry_run": self.dry_run,
        }
        if self.extra:
            out["extra"] = self.extra
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PublishResult":
        known = {
            "platform",
            "ok",
            "video_id",
            "url",
            "scheduled_at",
            "duration_ms",
            "error",
            "dry_run",
            "extra",
        }
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"PublishResult 含未知字段：{sorted(unknown)}")
        return cls(
            platform=data["platform"],
            ok=bool(data["ok"]),
            video_id=data.get("video_id"),
            url=data.get("url"),
            scheduled_at=data.get("scheduled_at"),
            duration_ms=data.get("duration_ms"),
            error=data.get("error"),
            dry_run=bool(data.get("dry_run", False)),
            extra=dict(data.get("extra") or {}),
        )

    def short(self) -> str:
        """单行摘要，用于 CLI 输出与日志。"""
        if self.ok:
            tail = self.url or self.video_id or "(dry-run)"
            flag = " [dry-run]" if self.dry_run else ""
            return f"✓ {self.platform:<12} {tail}{flag}"
        return f"✗ {self.platform:<12} {self.error}"


@dataclass
class PublishReport:
    """一次编排的汇总。这是 Agent 唯一需要读的东西。"""

    results: list[PublishResult] = field(default_factory=list)
    started_at: str = field(default_factory=_now_iso)
    finished_at: str | None = None
    contract_version: str = CONTRACT_VERSION

    # ---------- 汇总视图 ----------

    @property
    def succeeded(self) -> list[PublishResult]:
        return [r for r in self.results if r.ok]

    @property
    def failed(self) -> list[PublishResult]:
        return [r for r in self.results if not r.ok]

    @property
    def all_ok(self) -> bool:
        """空报告不算成功——避免「什么都没跑」被误判为通过。"""
        return bool(self.results) and not self.failed

    def add(self, result: PublishResult) -> None:
        self.results.append(result)

    def extend(self, results: Iterable[PublishResult]) -> None:
        self.results.extend(results)

    def close(self) -> "PublishReport":
        self.finished_at = _now_iso()
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "summary": {
                "total": len(self.results),
                "ok": len(self.succeeded),
                "failed": len(self.failed),
                "failed_platforms": [r.platform for r in self.failed],
            },
            "results": [r.to_dict() for r in self.results],
        }

    def render(self) -> str:
        """给人和 Agent 看的多行文本摘要。"""
        lines = [r.short() for r in self.results]
        lines.append(
            f"— {len(self.succeeded)}/{len(self.results)} 成功"
            + (f"，失败：{', '.join(r.platform for r in self.failed)}" if self.failed else "")
        )
        return "\n".join(lines)
