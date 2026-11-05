"""工具调用的超时、有限重试和熔断。

设计边界：
- 只对无状态、幂等的读工具重试。
- 有状态工具（fill_context_for_report）不重试、不缓存、不参与熔断。
- rag_summarize 内部已经走模型 SDK 的重试，工具层不再重复重试。
"""

from __future__ import annotations

import random
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Callable, TypeVar

from utils.config_handler import agent_conf

T = TypeVar("T")

DEFAULT_TOOL_TIMEOUT_SECONDS = float(agent_conf.get("tool_timeout_seconds", 30))
DEFAULT_MAX_RETRIES = int(agent_conf.get("tool_max_retries", 2))
DEFAULT_BACKOFF_MS = int(agent_conf.get("tool_retry_backoff_ms", 200))
DEFAULT_CIRCUIT_THRESHOLD = int(agent_conf.get("circuit_failure_threshold", 3))
DEFAULT_CIRCUIT_COOLDOWN_SECONDS = float(
    agent_conf.get("circuit_cooldown_seconds", 30)
)

# 只对这些纯读工具做工具层重试；模型重试由 ChatTongyi 自己负责。
RETRYABLE_TOOLS = {"get_order_status", "fetch_external_data"}
# 这些工具有状态或内部已经管理超时，不做外层线程超时。
TIMEOUT_EXCLUDED_TOOLS = {"rag_summarize", "fill_context_for_report"}
# 有状态工具不参与熔断统计。
CIRCUIT_EXCLUDED_TOOLS = {"fill_context_for_report"}

_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="tool-timeout")


class ToolBudgetExceeded(RuntimeError):
    """工具预算耗尽；由中间件转换成 error ToolMessage，不打断整个请求。"""

    def __init__(self, max_calls: int) -> None:
        self.max_calls = max_calls
        super().__init__(f"工具调用次数已达上限（{max_calls} 次）")


def is_transient_error(exc: BaseException) -> bool:
    """只把超时/连接类异常视为可重试的瞬时错误。"""
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    name = type(exc).__name__.casefold()
    return "timeout" in name or "connection" in name


def call_with_retry(
    func: Callable[[], T],
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    backoff_ms: int = DEFAULT_BACKOFF_MS,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = random.random,
    before_attempt: Callable[[int], None] | None = None,
    on_retry: Callable[[int, BaseException], None] | None = None,
) -> tuple[T, int]:
    """执行 func；瞬时错误最多重试 max_retries 次。

    返回 (结果, 重试次数)。before_attempt 在每次尝试前调用，
    用来把重试也计入工具预算。
    """
    attempts = 0
    while True:
        attempts += 1
        if before_attempt is not None:
            before_attempt(attempts)

        try:
            return func(), attempts - 1
        except Exception as exc:
            if attempts > max_retries or not is_transient_error(exc):
                raise

            delay_seconds = backoff_ms * (2 ** (attempts - 1)) / 1000
            if delay_seconds > 0:
                delay_seconds += jitter() * delay_seconds * 0.1
            if on_retry is not None:
                on_retry(attempts, exc)
            sleep(delay_seconds)


def run_with_timeout(
    func: Callable[[], T],
    timeout_seconds: float,
    *,
    executor: ThreadPoolExecutor | None = None,
) -> T:
    """在线程里执行 func；超时只停止等待，不能强杀线程。"""
    if timeout_seconds <= 0:
        return func()

    pool = executor or _EXECUTOR
    future = pool.submit(func)
    try:
        return future.result(timeout=timeout_seconds)
    except FuturesTimeoutError as exc:
        future.cancel()
        raise TimeoutError(f"工具执行超过 {timeout_seconds:g}s") from exc


class CircuitBreaker:
    """单个工具的熔断状态机：closed -> open -> half_open。"""

    def __init__(
        self,
        name: str,
        *,
        threshold: int = DEFAULT_CIRCUIT_THRESHOLD,
        cooldown_seconds: float = DEFAULT_CIRCUIT_COOLDOWN_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.name = name
        self.threshold = max(1, int(threshold))
        self.cooldown_seconds = max(0.0, float(cooldown_seconds))
        self.clock = clock
        self.failures = 0
        self.opened_at: float | None = None
        self.half_open = False

    @property
    def state(self) -> str:
        if self.opened_at is None:
            return "closed"
        return "half_open" if self.half_open else "open"

    def allow(self) -> bool:
        if self.opened_at is None:
            return True
        if self.clock() - self.opened_at < self.cooldown_seconds:
            return False
        if self.half_open:
            return False
        self.half_open = True
        return True

    def record_success(self) -> None:
        self.failures = 0
        self.opened_at = None
        self.half_open = False

    def record_failure(self) -> None:
        if self.half_open:
            self.opened_at = self.clock()
            self.half_open = False
            self.failures = self.threshold
            return

        self.failures += 1
        if self.failures >= self.threshold:
            self.opened_at = self.clock()

    def reset(self) -> None:
        self.failures = 0
        self.opened_at = None
        self.half_open = False


class CircuitBreakerRegistry:
    """按工具名维护熔断器；进程级共享，测试可整体重置。"""

    def __init__(
        self,
        *,
        threshold: int = DEFAULT_CIRCUIT_THRESHOLD,
        cooldown_seconds: float = DEFAULT_CIRCUIT_COOLDOWN_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.threshold = threshold
        self.cooldown_seconds = cooldown_seconds
        self.clock = clock
        self._breakers: dict[str, CircuitBreaker] = {}

    def get(self, name: str) -> CircuitBreaker:
        if name not in self._breakers:
            self._breakers[name] = CircuitBreaker(
                name,
                threshold=self.threshold,
                cooldown_seconds=self.cooldown_seconds,
                clock=self.clock,
            )
        return self._breakers[name]

    def reset(self) -> None:
        self._breakers.clear()


circuit_registry = CircuitBreakerRegistry()
