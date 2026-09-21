"""凭证管理。

两类凭证，性质完全不同，必须分开存：

1. **OAuth token**（YouTube / Dailymotion / TikTok 官方 API）
   含 ``refresh_token``，长期有效，``access_token`` 过期后自动刷新。

2. **Cookie**（抖音 / 快手 / B站 / TikTok 浏览器轨）
   **会过期**，且是这类系统最常见的日常故障来源。
   所以必须提供一条一分钟能跑完的重登命令，而不是让人去翻文档。

所有凭证文件都落在 ``secrets/`` 下，该目录已在 .gitignore 中被忽略。
本模块**任何路径下都不会把凭证值写进日志或异常信息**。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULT_SECRETS_DIR = "secrets"
ENV_FILE = ".env"

# 平台 → 需要的凭证类型
OAUTH_PLATFORMS = ("youtube", "dailymotion", "tiktok")
COOKIE_PLATFORMS = ("douyin", "kuaishou", "bilibili", "tiktok")

_REDACT_KEYS = (
    "secret",
    "token",
    "password",
    "cookie",
    "sessionid",
    "authorization",
    "client_key",
    "client_id",
    "api_key",
    "apikey",
)


def redact(value: Any) -> str:
    """把任意凭证值变成可安全打印的字符串。"""
    if value is None:
        return "<none>"
    s = str(value)
    if len(s) <= 8:
        return "***"
    return f"{s[:3]}…{s[-3:]} (len={len(s)})"


def looks_secret(key: str) -> bool:
    """键名是否像凭证。用于扫描与日志脱敏。"""
    k = key.lower()
    return any(marker in k for marker in _REDACT_KEYS)


def repo_root() -> Path:
    """仓库根目录（本文件位于 src/vidrelay/secrets.py）。"""
    return Path(__file__).resolve().parents[2]


def secrets_dir() -> Path:
    """凭证目录。可用 VIDRELAY_SECRETS_DIR 覆盖。"""
    raw = os.environ.get("VIDRELAY_SECRETS_DIR") or DEFAULT_SECRETS_DIR
    p = Path(raw)
    return p if p.is_absolute() else repo_root() / p


def ensure_dirs() -> Path:
    """确保凭证目录与 Cookie 子目录存在。"""
    d = secrets_dir()
    (d / "cookies").mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------- 路径


def oauth_token_path(platform: str) -> Path:
    return secrets_dir() / f"{platform}.json"


def cookie_path(platform: str, account: str) -> Path:
    safe = "".join(c for c in account if c.isalnum() or c in "-_") or "default"
    return secrets_dir() / "cookies" / f"{platform}_{safe}.json"


# ---------------------------------------------------------------- 读写


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    ensure_dirs()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    try:  # 尽力收紧权限（Windows 上无效果，POSIX 上有效）
        os.chmod(path, 0o600)
    except OSError:
        pass


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def save_oauth_token(platform: str, token: dict[str, Any]) -> Path:
    path = oauth_token_path(platform)
    _write_json(path, token)
    return path


def load_oauth_token(platform: str) -> dict[str, Any] | None:
    return _read_json(oauth_token_path(platform))


def save_cookies(platform: str, account: str, cookies: Any) -> Path:
    path = cookie_path(platform, account)
    _write_json(path, {"platform": platform, "account": account, "cookies": cookies})
    return path


def load_cookies(platform: str, account: str = "default") -> Any | None:
    data = _read_json(cookie_path(platform, account))
    return data.get("cookies") if data else None


# ---------------------------------------------------------------- 状态盘点


def credential_status() -> dict[str, dict[str, Any]]:
    """盘点各平台凭证是否就绪。供 ``vidrelay doctor`` 使用。

    只返回「有 / 没有」与文件路径，**绝不返回值本身**。
    """
    status: dict[str, dict[str, Any]] = {}

    for platform in OAUTH_PLATFORMS:
        path = oauth_token_path(platform)
        data = _read_json(path)
        status[f"{platform}:oauth"] = {
            "present": bool(data),
            "path": str(path),
            "has_refresh_token": bool(data and data.get("refresh_token")),
        }

    cookie_dir = secrets_dir() / "cookies"
    for platform in COOKIE_PLATFORMS:
        found = sorted(cookie_dir.glob(f"{platform}_*.json")) if cookie_dir.is_dir() else []
        status[f"{platform}:cookie"] = {
            "present": bool(found),
            "path": str(found[0]) if found else str(cookie_dir / f"{platform}_<account>.json"),
            "accounts": [p.stem[len(platform) + 1 :] for p in found],
        }

    return status


# ---------------------------------------------------------------- .env


def load_env_file(path: Path | None = None, override: bool = False) -> int:
    """极简 .env 解析器（避免为了 20 行逻辑引入依赖）。

    返回载入的条目数。已存在的环境变量默认不被覆盖。
    """
    env_path = path or (repo_root() / ENV_FILE)
    if not env_path.is_file():
        return 0

    count = 0
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key:
            continue
        if override or key not in os.environ:
            os.environ[key] = value
            count += 1
    return count


def missing_env(keys: list[str]) -> list[str]:
    """返回列表中尚未配置的环境变量名（不返回任何值）。"""
    return [k for k in keys if not os.environ.get(k)]
