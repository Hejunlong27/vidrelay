"""环境自检（M0.5）。

``vidrelay doctor`` 回答一个问题：**现在这台机器，能跑哪些平台？**

它逐项检查运行时、依赖、引擎、凭证，并把「缺什么、怎么补」直接写出来。
检查过程不修改任何东西，也不打印任何凭证值。
"""

from __future__ import annotations

import importlib.util
import platform
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from vidrelay import secrets
from vidrelay.platforms import PLATFORMS

MIN_PYTHON = (3, 10)

STATUS_ICON = {"ok": "✓", "warn": "!", "fail": "✗"}
STATUS_ORDER = {"fail": 0, "warn": 1, "ok": 2}


@dataclass
class Check:
    name: str
    status: str  # ok | warn | fail
    detail: str
    hint: str = ""

    def render(self) -> str:
        line = f"  {STATUS_ICON.get(self.status, '?')} {self.name:<34} {self.detail}"
        if self.hint and self.status != "ok":
            line += f"\n      → {self.hint}"
        return line


def _has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _check_python() -> Check:
    v = sys.version_info
    cur = f"{v.major}.{v.minor}.{v.micro}"
    if (v.major, v.minor) >= MIN_PYTHON:
        return Check("Python 版本", "ok", cur)
    need = ".".join(str(x) for x in MIN_PYTHON)
    return Check("Python 版本", "fail", cur, f"需要 Python >= {need}")


def _check_core_deps() -> list[Check]:
    out: list[Check] = []
    if _has_module("yaml"):
        out.append(Check("核心依赖 PyYAML", "ok", "已安装"))
    else:
        out.append(Check("核心依赖 PyYAML", "fail", "未安装", "pip install -e ."))
    return out


def _check_engines() -> list[Check]:
    out: list[Check] = []

    if _has_module("playwright"):
        out.append(Check("引擎 Playwright", "ok", "已安装（国内 3 平台需要）"))
    else:
        out.append(
            Check(
                "引擎 Playwright",
                "warn",
                "未安装",
                "国内链路（M3）才需要：pip install -e '.[cn]' && playwright install chromium",
            )
        )

    if _has_module("patchright"):
        out.append(Check("引擎 Patchright", "ok", "已安装"))
    else:
        out.append(
            Check(
                "引擎 Patchright",
                "warn",
                "未安装",
                "可选。部分平台的反检测效果更好，M2/M3 时再装",
            )
        )

    biliup = shutil.which("biliup")
    if biliup:
        out.append(Check("引擎 biliup", "ok", f"已安装（{biliup}）"))
    else:
        out.append(
            Check(
                "引擎 biliup",
                "warn",
                "未安装",
                "B站（M3）需要。注意用 Python 版 biliup/biliup，Rust 版 biliup-rs 已归档",
            )
        )

    return out


def _check_secrets_dir() -> Check:
    d = secrets.secrets_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe = d / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return Check("凭证目录可写", "fail", f"{d} 不可写（{exc}）", "检查目录权限或改 VIDRELAY_SECRETS_DIR")
    return Check("凭证目录可写", "ok", str(d))


def _check_gitignore() -> Check:
    gi = secrets.repo_root() / ".gitignore"
    if not gi.is_file():
        return Check(".gitignore 存在", "fail", "缺失", "缺少它会直接把凭证提交上去")
    text = gi.read_text(encoding="utf-8")
    needed = ["secrets/", ".env", "cookies/"]
    missing = [n for n in needed if n not in text]
    if missing:
        return Check(
            ".gitignore 覆盖凭证", "fail", f"缺少规则：{', '.join(missing)}", "补上再提交任何东西"
        )
    return Check(".gitignore 覆盖凭证", "ok", "secrets/ .env cookies/ 均已忽略")


def _check_env_file() -> Check:
    env_path = secrets.repo_root() / secrets.ENV_FILE
    if env_path.is_file():
        return Check(".env 文件", "ok", "存在（内容不会被打印）")
    example = secrets.repo_root() / ".env.example"
    hint = "cp .env.example .env 后填入真实值" if example.is_file() else "缺少 .env.example"
    return Check(".env 文件", "warn", "不存在", hint)


def _check_credentials() -> list[Check]:
    out: list[Check] = []
    status = secrets.credential_status()

    for platform_key, meta in PLATFORMS.items():
        name = meta.display_name
        oauth = status.get(f"{platform_key}:oauth")
        cookie = status.get(f"{platform_key}:cookie")

        if oauth is None and cookie is None:
            continue

        if oauth and oauth["present"]:
            extra = "（含 refresh_token）" if oauth["has_refresh_token"] else "（无 refresh_token）"
            out.append(Check(f"凭证 · {name} OAuth", "ok", f"已就绪{extra}"))
        elif cookie and cookie["present"]:
            accounts = ", ".join(cookie["accounts"]) or "?"
            out.append(Check(f"凭证 · {name} Cookie", "ok", f"已就绪（账号：{accounts}）"))
            out.append(
                Check(
                    f"凭证 · {name} OAuth",
                    "warn",
                    "未配置",
                    "如需走官方 API，请完成一次 OAuth 授权",
                )
            )
        else:
            out.append(
                Check(
                    f"凭证 · {name}",
                    "warn",
                    "未配置",
                    "该平台对应的里程碑开工时再配",
                )
            )

    return out


def run_all() -> list[Check]:
    checks: list[Check] = [_check_python()]
    checks += _check_core_deps()
    checks += _check_engines()
    checks.append(_check_secrets_dir())
    checks.append(_check_gitignore())
    checks.append(_check_env_file())
    checks += _check_credentials()
    return checks


def render(checks: list[Check], header: str | None = None) -> str:
    lines: list[str] = []
    if header:
        lines.append(header)
    lines.append(
        f"  {'运行时':<36} Python {platform.python_version()} · {platform.system()} {platform.release()}"
    )
    lines.append("")

    grouped = {"fail": [], "warn": [], "ok": []}
    for c in checks:
        grouped.setdefault(c.status, []).append(c)

    for status in ("fail", "warn", "ok"):
        for c in grouped.get(status, []):
            lines.append(c.render())

    fails = len(grouped.get("fail", []))
    warns = len(grouped.get("warn", []))
    lines.append("")
    if fails:
        lines.append(f"  结论：{fails} 项阻塞、{warns} 项待补。先修阻塞项。")
    elif warns:
        lines.append(f"  结论：无阻塞项，{warns} 项待补（多为后续里程碑才需要的依赖）。")
    else:
        lines.append("  结论：全部就绪。")
    return "\n".join(lines)


def main() -> int:
    checks = run_all()
    print(render(checks, header="vidrelay doctor — 环境自检"))
    return 1 if any(c.status == "fail" for c in checks) else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
