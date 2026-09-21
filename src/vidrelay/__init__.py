"""vidrelay — one video in, many platforms out."""

from vidrelay.contract import PublishReport, PublishResult
from vidrelay.platforms import PLATFORMS, Group, Platform

__version__ = "0.1.0"

__all__ = [
    "PLATFORMS",
    "Group",
    "Platform",
    "PublishReport",
    "PublishResult",
    "__version__",
]
