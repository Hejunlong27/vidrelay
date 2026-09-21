"""vidrelay 命令行入口（M0.1）。

设计原则：**CLI 是 Agent 与这套系统之间唯一的接口。**

Agent 只做两件事——生成 meta.yaml、跑一条命令读回一个 JSON。
中间过程一律不进入 Agent 上下文，这是把 token 从 50K–150K 压到 1K–3K 的关键。
因此每个命令都要支持 ``--json``，且输出结构稳定。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from vidrelay import __version__, doctor, netguard, secrets
from vidrelay.adapters import available_platforms, missing_platforms
from vidrelay.platforms import PLATFORMS, Group, platforms_in
from vidrelay.runner import OrchestrationError, RunOptions, run
from vidrelay.schema import validate_file

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2


# ---------------------------------------------------------------- publish


def cmd_publish(args: argparse.Namespace) -> int:
    secrets.load_env_file()

    group = Group(args.group) if args.group else None

    opts = RunOptions(
        meta_path=args.meta,
        group=group,
        only=args.platform or None,
        dry_run=args.dry_run,
        skip_netguard=args.skip_netguard,
        save_report=not args.no_save,
    )

    try:
        report = run(opts)
    except OrchestrationError as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        else:
            print(f"无法执行：\n{exc}", file=sys.stderr)
        return EXIT_USAGE

    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(report.render())

    return EXIT_OK if report.all_ok else EXIT_FAILED


# ---------------------------------------------------------------- validate


def cmd_validate(args: argparse.Namespace) -> int:
    result = validate_file(args.meta)

    if args.json:
        print(
            json.dumps(
                {
                    "ok": result.ok,
                    "errors": [
                        {"path": i.path, "message": i.message, "line": i.line}
                        for i in result.errors
                    ],
                    "warnings": [
                        {"path": i.path, "message": i.message, "line": i.line}
                        for i in result.warnings
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return EXIT_OK if result.ok else EXIT_FAILED

    if not result.issues:
        print(f"✓ {args.meta} 校验通过")
        return EXIT_OK

    for issue in result.issues:
        print(issue.render(args.meta))

    tail = f"{len(result.errors)} 个错误"
    if result.warnings:
        tail += f"、{len(result.warnings)} 个警告"
    print(f"\n{'✗' if result.errors else '!'} {args.meta}：{tail}")
    return EXIT_OK if result.ok else EXIT_FAILED


# ---------------------------------------------------------------- doctor


def cmd_doctor(args: argparse.Namespace) -> int:
    secrets.load_env_file()
    checks = doctor.run_all()

    if args.json:
        print(
            json.dumps(
                {
                    "ok": not any(c.status == "fail" for c in checks),
                    "checks": [
                        {"name": c.name, "status": c.status, "detail": c.detail, "hint": c.hint}
                        for c in checks
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(doctor.render(checks, header=f"vidrelay doctor — 环境自检 (v{__version__})"))

    return EXIT_OK if not any(c.status == "fail" for c in checks) else EXIT_FAILED


# ---------------------------------------------------------------- platforms


def cmd_platforms(args: argparse.Namespace) -> int:
    if args.json:
        payload = {
            "platforms": [
                {
                    "key": p.key,
                    "name": p.display_name,
                    "group": p.group.value,
                    "engine": p.engine,
                    "access": p.access,
                    "official_api": p.official_api,
                    "needs_vpn_off": p.needs_vpn_off,
                    "adapter_ready": p.key in available_platforms(),
                }
                for p in PLATFORMS.values()
            ],
            "adapters_ready": available_platforms(),
            "adapters_missing": missing_platforms(),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return EXIT_OK

    ready = set(available_platforms())
    for group in (Group.CN, Group.OVERSEA):
        label = "国内组（必须国内直连，禁 VPN）" if group is Group.CN else "海外组（需要海外出口 IP）"
        print(f"\n{label}")
        for p in platforms_in(group):
            mark = "已实现" if p.key in ready else "待实现"
            print(f"  [{mark}] {p.key:<12} {p.access:<6} {p.display_name:<11} {p.engine}")
    print()
    return EXIT_OK


# ---------------------------------------------------------------- netcheck


def cmd_netcheck(args: argparse.Namespace) -> int:
    secrets.load_env_file()
    status = netguard.detect()

    if args.json:
        print(
            json.dumps(
                {
                    "ok": status.ok,
                    "country": status.country,
                    "ip_masked": status.ip_masked,
                    "source": status.source,
                    "message": status.message,
                    "verdicts": {
                        g.value: netguard.check(g, status).allowed for g in (Group.CN, Group.OVERSEA)
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return EXIT_OK if status.ok else EXIT_FAILED

    print(f"出口网络探测：{status.render()}")
    for group in (Group.CN, Group.OVERSEA):
        v = netguard.check(group, status)
        print(f"  {'✓' if v.allowed else '✗'} {group.value:<8} {v.reason}")
    return EXIT_OK if status.ok else EXIT_FAILED


# ---------------------------------------------------------------- 解析器


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vidrelay",
        description="一条视频，多平台分发。契约驱动的发布编排器。",
        epilog="示例：vidrelay publish meta.yaml --group cn --dry-run",
    )
    parser.add_argument("--version", action="version", version=f"vidrelay {__version__}")

    sub = parser.add_subparsers(dest="command", metavar="<command>")

    p_pub = sub.add_parser("publish", help="按 meta 文件发布到多个平台")
    p_pub.add_argument("meta", help="meta.yaml 路径")
    p_pub.add_argument(
        "--group",
        choices=[g.value for g in Group],
        help="只跑某一组。国内组与海外组的网络要求互斥，必须分开跑",
    )
    p_pub.add_argument("--platform", action="append", help="只跑指定平台，可重复")
    p_pub.add_argument("--dry-run", action="store_true", help="预演：填完表单但不真正发布")
    p_pub.add_argument(
        "--skip-netguard", action="store_true", help="跳过出口 IP 校验（不建议，仅调试用）"
    )
    p_pub.add_argument("--no-save", action="store_true", help="不把本次结果写入 runs/")
    p_pub.add_argument("--json", action="store_true", help="以 JSON 输出（给 Agent 用）")
    p_pub.set_defaults(func=cmd_publish)

    p_val = sub.add_parser("validate", help="校验 meta 文件，报错带行号")
    p_val.add_argument("meta", help="meta.yaml 路径")
    p_val.add_argument("--json", action="store_true")
    p_val.set_defaults(func=cmd_validate)

    p_doc = sub.add_parser("doctor", help="环境自检：依赖、引擎、凭证")
    p_doc.add_argument("--json", action="store_true")
    p_doc.set_defaults(func=cmd_doctor)

    p_plat = sub.add_parser("platforms", help="列出平台与适配器实现状态")
    p_plat.add_argument("--json", action="store_true")
    p_plat.set_defaults(func=cmd_platforms)

    p_net = sub.add_parser("netcheck", help="探测出口 IP 归属，判断当前能跑哪一组")
    p_net.add_argument("--json", action="store_true")
    p_net.set_defaults(func=cmd_netcheck)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_USAGE

    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
