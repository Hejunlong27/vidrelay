"""平台适配器包。

M1 起在这里逐个新增文件，每个文件注册一个适配器，例如：

    # adapters/youtube.py
    from vidrelay.adapters.base import Adapter, PublishRequest, register

    @register
    class YouTubeAdapter(Adapter):
        platform_key = "youtube"

        def publish(self) -> PublishResult:
            ...

新增平台不需要改动 CLI 或编排层。
"""

from vidrelay.adapters.base import (
    Adapter,
    NotImplementedAdapter,
    PublishRequest,
    available_platforms,
    build,
    get_adapter_class,
    missing_platforms,
    register,
)

__all__ = [
    "Adapter",
    "NotImplementedAdapter",
    "PublishRequest",
    "available_platforms",
    "build",
    "get_adapter_class",
    "missing_platforms",
    "register",
]
