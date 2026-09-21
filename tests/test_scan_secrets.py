"""凭证扫描器自身的测试。

一个「永远说干净」的扫描器毫无价值，所以规则必须有测试覆盖。

注意：本文件里出现的样例凭证都是**构造出来的假值**，
并且逐行加了 `# scan:allow` 标记——这正是该标记存在的用途。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCANNER_PATH = Path(__file__).resolve().parents[1] / "scripts" / "scan_secrets.py"
_spec = importlib.util.spec_from_file_location("scan_secrets", _SCANNER_PATH)
assert _spec and _spec.loader
scan_secrets = importlib.util.module_from_spec(_spec)
# dataclass 需要能在 sys.modules 里找到自己的模块，否则解析类型注解时会炸
sys.modules[_spec.name] = scan_secrets
_spec.loader.exec_module(scan_secrets)


def matched_rules(text: str) -> set[str]:
    """返回这段文本会命中哪些规则名。"""
    return {rule.name for rule in scan_secrets.RULES if rule.pattern.search(text)}


def test_fake_google_api_key() -> None:  # scan:allow
    line = 'key = "AIzaSyD-1234567890abcdefghijklmnopqrst"'  # scan:allow
    assert "Google API Key" in matched_rules(line)


def test_short_lookalike_is_not_flagged() -> None:
    # 长度不够的相似串不应误报
    assert "Google API Key" not in matched_rules('x = "AIzaShort"')


def test_google_oauth_access_token() -> None:  # scan:allow
    line = "token = ya29.a0AfH6SMBxxxxxxxxxxxxxxxxxxxxxxxxxxxx"  # scan:allow
    assert "Google OAuth Access Token" in matched_rules(line)


def test_refresh_token_assignment() -> None:  # scan:allow
    line = 'refresh_token = "1//0gL9xK2mQvB8pRtYzAbCdEfGhIjKlMnOpQrSt"'  # scan:allow
    assert "OAuth refresh_token 赋值" in matched_rules(line)


def test_platform_cookie() -> None:  # scan:allow
    line = "SESSDATA=abc123def456ghi789"  # scan:allow
    assert "平台 Cookie 字段" in matched_rules(line)


def test_hardcoded_client_secret() -> None:  # scan:allow
    line = 'client_secret = "GOCSPX-abcdefghijklmnop"'  # scan:allow
    assert "疑似硬编码的密钥赋值" in matched_rules(line)


def test_empty_value_is_not_flagged() -> None:
    # .env.example 里的空值不应该报警
    assert matched_rules("YOUTUBE_CLIENT_SECRET=") == set()
    assert matched_rules("VIDRELAY_SECRETS_DIR=secrets") == set()


def test_github_token() -> None:  # scan:allow
    line = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij"  # scan:allow
    assert "GitHub Token" in matched_rules(line)


def test_private_key_block() -> None:  # scan:allow
    line = "-----BEGIN RSA PRIVATE KEY-----"  # scan:allow
    assert "私钥文件内容" in matched_rules(line)


@pytest.mark.parametrize(
    "line",
    [
        r"video: C:\Users\someuser\videos\a.mp4",  # scan:allow
        r"path = 'D:/Users/someuser/a.mp4'",  # scan:allow
        "home: /home/someuser/videos",  # scan:allow
        "mac: /Users/someuser/Movies/a.mp4",  # scan:allow
    ],
)
def test_local_absolute_paths(line: str) -> None:
    """本机绝对路径会泄露用户名，属于必须提示的一类。"""
    rules = matched_rules(line)
    assert "本机绝对路径（泄露用户名）" in rules or "类 Unix 家目录绝对路径" in rules


def test_generic_placeholder_path_is_not_flagged() -> None:
    # 示例文件里的占位路径不应误报
    assert matched_rules('video: "D:/videos/ep01.mp4"') == set()


def test_phone_number_is_low_severity() -> None:
    rule = next(r for r in scan_secrets.RULES if r.name == "中国大陆手机号")
    assert rule.severity == "low"
    assert rule.pattern.search("联系 13800138000")  # scan:allow
    assert not rule.pattern.search("订单号 138001380000")


def test_allow_marker_skips_line(tmp_path: Path, monkeypatch) -> None:
    """带标记的行应被跳过，不带标记的应被命中。

    这里刻意用拼接方式构造样例值，避免本文件自身出现完整可命中的字面量
    （否则扫描器扫仓库时会报警——那属于「扫描器工作正常」而不是 bug）。
    """
    monkeypatch.setattr(scan_secrets, "REPO_ROOT", tmp_path)
    fake_key = "AIza" + "SyD-1234567890abcdefghijklmnopqrst"
    f = tmp_path / "probe.py"
    f.write_text(
        f'a = "{fake_key}"\n'  # 无标记 → 应命中
        f'b = "{fake_key}"  # scan:allow\n',  # 有标记 → 应跳过
        encoding="utf-8",
    )
    findings = scan_secrets.scan_file(f)
    assert len(findings) == 1
    assert findings[0].line == 1


def test_excerpt_truncates_long_secrets() -> None:
    """摘要不能把完整凭证打印出来。"""
    line = 'k = "AIzaSyD-1234567890abcdefghijklmnopqrstUVWXY"'  # scan:allow
    rule = next(r for r in scan_secrets.RULES if r.name == "Google API Key")
    m = rule.pattern.search(line)
    assert m
    excerpt = scan_secrets._excerpt(line, m)
    assert "已截断" in excerpt
    assert m.group(0) not in excerpt


def test_binary_and_large_files_are_skipped(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(scan_secrets, "REPO_ROOT", tmp_path)
    big = tmp_path / "big.txt"
    big.write_text("AIzaSyD-1234567890abcdefghijklmnopqrst", encoding="utf-8")  # scan:allow
    monkeypatch.setattr(scan_secrets, "MAX_BYTES", 5)
    assert scan_secrets.scan_file(big) == []


def test_skip_suffixes() -> None:
    assert ".mp4" in scan_secrets.SKIP_SUFFIXES
    assert ".zip" in scan_secrets.SKIP_SUFFIXES
