#!/usr/bin/env python3
"""提交前凭证扫描。

**为什么需要它：** .gitignore 只能挡住「已知的路径」。
真正危险的是「把 token 写进了一个本来该提交的文件里」——
比如调试时临时 print 出来的 refresh_token，或者 README 里的示例填了真值。
这个脚本扫的是内容，不是路径。

用法：
    python scripts/scan_secrets.py            # 只扫 git 已跟踪的文件（推荐）
    python scripts/scan_secrets.py --all      # 扫整个工作区
    python scripts/scan_secrets.py --staged   # 只扫暂存区（pre-commit 用）

退出码：发现高危项返回 1，只有低危提示返回 0。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "build",
    "dist",
    "secrets",
    "runs",
    "logs",
}

SKIP_SUFFIXES = {
    ".mp4", ".mov", ".mkv", ".avi", ".flv", ".webm", ".m4v",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico",
    ".zip", ".tar", ".gz", ".7z", ".whl", ".exe", ".dll",
    ".lock",
}

MAX_BYTES = 2_000_000
ALLOW_MARKER = "scan:allow"


@dataclass
class Rule:
    name: str
    pattern: re.Pattern[str]
    severity: str  # high | low
    hint: str


RULES: list[Rule] = [
    Rule(
        "私钥文件内容",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
        "high",
        "私钥绝不能入库。请删除该文件并轮换密钥。",
    ),
    Rule(
        "Google API Key",
        re.compile(r"\bAIza[0-9A-Za-z_\-]{30,40}\b"),
        "high",
        "YouTube Data API 的 API Key 泄露。请到 Google Cloud 撤销并重建。",
    ),
    Rule(
        "Google OAuth Access Token",
        re.compile(r"\bya29\.[0-9A-Za-z_\-]{20,}"),
        "high",
        "OAuth access token 泄露。",
    ),
    Rule(
        "GitHub Token",
        re.compile(r"\b(gh[pousr]_[0-9A-Za-z]{36,}|github_pat_[0-9A-Za-z_]{20,})\b"),
        "high",
        "GitHub 个人访问令牌泄露。",
    ),
    Rule(
        "OpenAI 风格密钥",
        re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
        "high",
        "API 密钥泄露。",
    ),
    Rule(
        "AWS Access Key",
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        "high",
        "AWS 凭证泄露。",
    ),
    Rule(
        "Slack Token",
        re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}"),
        "high",
        "Slack token 泄露。",
    ),
    Rule(
        "JWT",
        re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),
        "high",
        "疑似 JWT（可能含会话凭证）。",
    ),
    Rule(
        "OAuth refresh_token 赋值",
        re.compile(r"""["']?refresh_token["']?\s*[:=]\s*["'][^"']{10,}["']"""),
        "high",
        "refresh_token 是长期凭证，绝不能入库。",
    ),
    Rule(
        "疑似硬编码的密钥赋值",
        re.compile(
            r"""(?i)\b(client_secret|client_key|api_key|apikey|access_token|auth_token|password|passwd)\b"""
            r"""\s*[:=]\s*["'][^"'\s]{12,}["']"""
        ),
        "high",
        "疑似硬编码凭证。改为从环境变量或 secrets/ 读取。",
    ),
    Rule(
        "平台 Cookie 字段",
        re.compile(
            r"\b(SESSDATA|bili_jct|DedeUserID|sessionid|odin_tt|ttwid|msToken|passport_csrf_token)"
            r"\s*[:=]\s*[\"']?[A-Za-z0-9_\-%]{8,}"
        ),
        "high",
        "平台登录 Cookie 泄露。Cookie 应只存在于 secrets/cookies/ 下。",
    ),
    Rule(
        "本机绝对路径（泄露用户名）",
        re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+[A-Za-z0-9_.\-]+"),
        "low",
        "会把本机用户名写进公开仓库。建议改用相对路径或占位符。",
    ),
    Rule(
        "类 Unix 家目录绝对路径",
        re.compile(r"(?<![\w/])/(?:home|Users)/[A-Za-z0-9_.\-]+"),
        "low",
        "会把本机用户名写进公开仓库。",
    ),
    Rule(
        "中国大陆手机号",
        re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
        "low",
        "疑似手机号（个人信息）。确认是否必须提交。",
    ),
]


@dataclass
class Finding:
    path: str
    line: int
    rule: str
    severity: str
    hint: str
    excerpt: str

    def render(self) -> str:
        tag = "高危" if self.severity == "high" else "提示"
        return (
            f"  [{tag}] {self.path}:{self.line}  {self.rule}\n"
            f"        {self.excerpt}\n"
            f"        → {self.hint}"
        )


def _excerpt(line: str, match: re.Match[str]) -> str:
    """只展示匹配片段的前后少量字符，避免把完整凭证打印出来。"""
    start = max(0, match.start() - 12)
    end = min(len(line), match.end() + 12)
    snippet = line[start:end].strip()
    if match.end() - match.start() > 12:
        snippet = (
            line[start : match.start()].strip()
            + " <<"
            + line[match.start() : match.start() + 6]
            + "…已截断>> "
            + line[match.end() : end].strip()
        )
    return snippet[:160]


def _tracked_files() -> list[Path]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=REPO_ROOT,
            capture_output=True,
            check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    return [REPO_ROOT / p for p in out.decode("utf-8", "replace").split("\0") if p]


def _staged_files() -> list[Path]:
    try:
        out = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR"],
            cwd=REPO_ROOT,
            capture_output=True,
            check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    return [REPO_ROOT / p for p in out.decode("utf-8", "replace").split("\0") if p]


def _walk_files() -> list[Path]:
    found: list[Path] = []
    for p in REPO_ROOT.rglob("*"):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.relative_to(REPO_ROOT).parts):
            continue
        found.append(p)
    return found


def scan_file(path: Path) -> list[Finding]:
    rel = path.relative_to(REPO_ROOT).as_posix()
    if path.suffix.lower() in SKIP_SUFFIXES:
        return []
    try:
        if path.stat().st_size > MAX_BYTES:
            return []
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    findings: list[Finding] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if ALLOW_MARKER in line:
            continue
        for rule in RULES:
            m = rule.pattern.search(line)
            if m:
                findings.append(
                    Finding(
                        path=rel,
                        line=lineno,
                        rule=rule.name,
                        severity=rule.severity,
                        hint=rule.hint,
                        excerpt=_excerpt(line, m),
                    )
                )
    return findings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="扫描仓库中的凭证与个人信息泄露")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--all", action="store_true", help="扫整个工作区")
    mode.add_argument("--staged", action="store_true", help="只扫暂存区（pre-commit）")
    args = ap.parse_args(argv)

    if args.staged:
        files, label = _staged_files(), "暂存区"
    elif args.all:
        files, label = _walk_files(), "整个工作区"
    else:
        files = _tracked_files()
        label = "git 已跟踪文件"
        if not files:
            print("提示：当前还不是 git 仓库或没有任何已跟踪文件，改为扫描整个工作区。")
            files, label = _walk_files(), "整个工作区"

    findings: list[Finding] = []
    for f in files:
        findings.extend(scan_file(f))

    high = [f for f in findings if f.severity == "high"]
    low = [f for f in findings if f.severity == "low"]

    print(f"凭证扫描 — 范围：{label}（{len(files)} 个文件）")
    if not findings:
        print("  ✓ 未发现凭证或个人信息泄露。")
        return 0

    for f in high:
        print(f.render())
    for f in low:
        print(f.render())

    print()
    print(f"  结果：{len(high)} 项高危、{len(low)} 项提示。")
    if high:
        print("  高危项必须先清理，再提交 / 推送。")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
