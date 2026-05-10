"""通用工具：重试装饰器、错误分级等。"""
from __future__ import annotations

import logging
import time
from functools import wraps
from typing import Any, Callable, Optional, Type

log = logging.getLogger(__name__)


class RetryExhausted(RuntimeError):
    """重试耗尽后的最终异常。"""

    def __init__(self, last_err: Exception, attempts: int):
        self.last_err = last_err
        self.attempts = attempts
        super().__init__(f"重试 {attempts} 次后失败: {last_err}")


def retry(
    max_retries: int = 3,
    backoff_base: float = 1.0,
    backoff_max: float = 30.0,
    exceptions: tuple[Type[Exception], ...] = (Exception,),
    on_retry: Optional[Callable[[int, Exception], None]] = None,
) -> Callable:
    """带指数退避的重试装饰器。

    Args:
        max_retries: 最大重试次数（不含首次调用）
        backoff_base: 退避基数（秒），实际等待 = min(base * 2^attempt, backoff_max）
        backoff_max: 最大退避时间（秒）
        exceptions: 触发重试的异常类型元组
        on_retry: 可选回调，签名 (attempt, exception) -> None，用于自定义日志
    """

    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_err: Exception | None = None
            for attempt in range(1, max_retries + 1):
                try:
                    return fn(*args, **kwargs)
                except exceptions as e:
                    last_err = e
                    if attempt == max_retries:
                        break
                    wait = min(backoff_base * (2 ** (attempt - 1)), backoff_max)
                    if on_retry:
                        on_retry(attempt, e)
                    else:
                        log.warning(
                            "%s 第 %d/%d 次失败: %s — %.1fs 后重试",
                            fn.__name__, attempt, max_retries, e, wait,
                        )
                    time.sleep(wait)
            raise RetryExhausted(last_err, max_retries)  # type: ignore[arg-type]

        return wrapper

    return decorator
