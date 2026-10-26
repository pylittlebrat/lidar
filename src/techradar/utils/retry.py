"""指数退避重试装饰器（供采集器与网络调用使用）。"""

from __future__ import annotations

import functools
import logging
import random
import time
from collections.abc import Callable, Sequence
from typing import Any, TypeVar

T = TypeVar("T")

logger = logging.getLogger("techradar.retry")


def retry(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    exceptions: Sequence[type[BaseException]] = (Exception,),
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """失败后按指数退避重试（含随机抖动）。

    :param max_attempts: 最大尝试次数（含第一次）
    :param base_delay: 初始退避秒数
    :param max_delay: 退避上限秒数
    :param exceptions: 仅对这些异常类型重试
    """

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            attempt = 0
            while True:
                attempt += 1
                try:
                    return fn(*args, **kwargs)
                except exceptions as exc:  # type: ignore[misc]
                    if attempt >= max_attempts:
                        raise
                    delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
                    delay = delay * (0.5 + random.random() / 2)
                    logger.warning(
                        "%s 第 %d/%d 次失败: %s；%.1fs 后重试",
                        fn.__name__,
                        attempt,
                        max_attempts,
                        exc,
                        delay,
                    )
                    time.sleep(delay)

        return wrapper

    return decorator