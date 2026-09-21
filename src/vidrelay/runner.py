"""编排层：把 meta.yaml 变成一次多平台发布。

职责边界很清楚：
- 读 meta、校验、按分组筛平台
- **执行前做网络校验**（netguard）
- 并发调用适配器，收集结果
- 产出统一的 PublishReport

它不关心任何平台的具体实现——那是适配器的事。
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from vidrelay import netguard
from vidrelay.adapters import PublishRequest, build
from vidrelay.contract import PublishReport, PublishResult
from vidrelay.platforms import Group, Platform, resolve
from vidrelay.schema import MetaValidationError, load_and_validate
from vidrelay.secrets import repo_root

MAX_WORKERS = 6


class OrchestrationError(Exception):
    """编排层面的错误（配置、分组、网络），与平台执行失败区分开。"""


@dataclass
class RunOptions:
    meta_path: str
    group: Group | None = None
    only: list[str] | None = None
    dry_run: bool = False
    skip_netguard: bool = False
    save_report: bool = True


def _select_platforms(data: dict, opts: RunOptions) -> list[Platform]:
    declared = list(data.get("platforms") or {})

    if opts.only:
        chosen = []
        for key in opts.only:
            p = resolve(key)
            if key not in declared:
                raise OrchestrationError(
                    f"命令行指定了 {key}，但 meta 文件里没有 platforms.{key} 的配置"
                )
            chosen.append(p)
    else:
        chosen = [resolve(k) for k in declared]

    if opts.group is not None:
        chosen = [p for p in chosen if p.group is opts.group]
        if not chosen:
            raise OrchestrationError(
                f"分组 {opts.group.value} 下没有任何已配置的平台。"
                f"meta 里配置的是：{', '.join(declared)}"
            )

    return chosen


def _check_network(platforms: Iterable[Platform], opts: RunOptions) -> None:
    """按分组做网络校验。国内组与海外组不能在同一次运行里混。"""
    groups = {p.group for p in platforms}

    if len(groups) > 1:
        raise OrchestrationError(
            "国内组与海外组不能在同一个命令里一起执行——两者的出口 IP 要求是互斥的。\n"
            "请分两次运行：\n"
            "  1) 断开 VPN，执行 vidrelay publish <meta> --group cn\n"
            "  2) 连上海外节点，执行 vidrelay publish <meta> --group oversea"
        )

    if opts.skip_netguard:
        return

    group = next(iter(groups))
    verdict = netguard.check(group)
    if not verdict.allowed:
        raise OrchestrationError(f"网络校验未通过：{verdict.reason}")


def _to_request(platform: Platform, data: dict, opts: RunOptions) -> PublishRequest:
    cfg = (data.get("platforms") or {}).get(platform.key) or {}
    common = data.get("common") or {}

    options = {
        k: v
        for k, v in cfg.items()
        if k not in {"title", "desc", "tags", "cover", "schedule", "extra"}
    }
    options.update(cfg.get("extra") or {})

    publish_at = common.get("publish_at") if cfg.get("schedule", True) else None

    return PublishRequest(
        platform=platform,
        video=str(data.get("video") or ""),
        title=str(cfg.get("title") or ""),
        desc=str(cfg.get("desc") or ""),
        tags=list(cfg.get("tags") or []),
        cover=cfg.get("cover") or data.get("cover"),
        publish_at=publish_at,
        dry_run=opts.dry_run,
        account=common.get("account"),
        options=options,
    )


def run(opts: RunOptions) -> PublishReport:
    """执行一次编排。"""
    try:
        data = load_and_validate(opts.meta_path)
    except MetaValidationError as exc:
        raise OrchestrationError(str(exc)) from exc

    platforms = _select_platforms(data, opts)
    _check_network(platforms, opts)

    report = PublishReport()
    requests = [_to_request(p, data, opts) for p in platforms]

    def execute(req: PublishRequest) -> PublishResult:
        # build() 对未实现的平台返回 NotImplementedAdapter，
        # 它同样走 preflight + try 这条路径，不需要特判分支。
        adapter = build(req)
        problems = adapter.preflight()
        if problems:
            return PublishResult.failure(
                req.platform.key,
                "；".join(problems),
                dry_run=req.dry_run,
            )
        try:
            return adapter.publish()
        except Exception as exc:  # 适配器不该抛异常，但兜住它，别让一个平台拖垮全局
            return PublishResult.failure(
                req.platform.key,
                f"适配器异常：{type(exc).__name__}: {exc}",
                dry_run=req.dry_run,
            )

    # 适配器之间彼此独立 → 并发执行，单点失败不影响其他
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, max(1, len(requests)))) as pool:
        futures = {pool.submit(execute, req): req for req in requests}
        for future in as_completed(futures):
            report.add(future.result())

    # 结果顺序稳定，便于 diff
    report.results.sort(key=lambda r: r.platform)
    report.close()

    if opts.save_report:
        _save(report, opts)

    return report


def _save(report: PublishReport, opts: RunOptions) -> Path:
    out_dir = repo_root() / "runs"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"{stamp}.json"

    payload = report.to_dict()
    payload["meta_path"] = opts.meta_path
    payload["dry_run"] = opts.dry_run
    payload["group"] = opts.group.value if opts.group else None

    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
