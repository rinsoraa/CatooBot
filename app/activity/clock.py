"""Phase 6A §二十二/§二十四：世界时钟（World Clock）与可注入的假时钟。

角色世界的"时间"必须**只有一处**（§二十二：项目已有就复用 —— 检查过：仓库里没有
``WorldClock`` 这个类，只有 ``RuntimeScheduler`` 推进真实时间，所以这里实现**最小**的那个）。

职责**仅限**读时间：

* ``now()`` —— 单调的真实时间（秒，UNIX 时间戳语义）
* ``elapsed_since(ts)`` —— 从某个时间戳到现在过了多久
* ``period()`` —— 一天里的语义时段（morning / afternoon / evening / night）
* ``timezone()`` —— 可配置时区（默认 ``Asia/Singapore``）

它**不认识**活动、Episode、数据库 —— 上层拿 ``now()`` 去算生命周期。
测试用 :class:`FakeClock` 直接 ``advance(...)``，**绝不** ``sleep(3600)``（§二十四）。
"""

from __future__ import annotations

import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

#: 默认时区（§二十二：Asia/Singapore，但可配置）
DEFAULT_TIMEZONE = "Asia/Singapore"

#: 语义时段划分（左闭右开，按本地小时）
PERIODS: tuple[tuple[int, int, str], ...] = (
    (5, 12, "morning"),
    (12, 18, "afternoon"),
    (18, 23, "evening"),
    (23, 24, "night"),
    (0, 5, "night"),
)


def period_of(hour: int) -> str:
    """本地小时 → 语义时段（同一个函数给真时钟和假时钟用，绝不两套口径）。"""
    for start, end, name in PERIODS:
        if start <= hour < end:
            return name
    return "night"


class WorldClock:
    """项目里唯一的世界时钟（真实时间；时区可配置）。"""

    def __init__(self, timezone: str = DEFAULT_TIMEZONE) -> None:
        self._timezone = str(timezone or DEFAULT_TIMEZONE)
        self.set_timezone(self._timezone)

    # ------------------------------------------------------------ 配置

    @property
    def timezone(self) -> str:
        return self._timezone

    def set_timezone(self, timezone: str) -> None:
        """设置时区；名字不认识就回落到默认（绝不因为配置写错就让启动失败）。"""
        name = str(timezone or DEFAULT_TIMEZONE)
        try:
            self._tz = ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError, KeyError):
            self._tz = ZoneInfo(DEFAULT_TIMEZONE)
            name = DEFAULT_TIMEZONE
        self._timezone = name

    # ------------------------------------------------------------ 读时间

    def now(self) -> float:
        """当前时间（秒）。"""
        return float(time.time())

    def elapsed_since(self, since: float) -> float:
        """从 ``since`` 到现在过了多少秒（``since`` <= 0 时返回 0）。"""
        start = float(since or 0.0)
        if start <= 0:
            return 0.0
        return max(0.0, self.now() - start)

    def local_hour(self, at: float | None = None) -> int:
        """本地小时（0-23）。"""
        moment = self.now() if at is None else float(at)
        return time.localtime(moment).tm_hour

    def period(self, at: float | None = None) -> str:
        """语义时段（按**配置时区**换算，而不是机器本地时区）。"""
        moment = self.now() if at is None else float(at)
        import datetime as _dt

        local = _dt.datetime.fromtimestamp(moment, tz=self._tz)
        return period_of(local.hour)

    def day_key(self, at: float | None = None) -> str:
        """本地日期 ``YYYYMMDD``（Episode ID 的日期段用它）。"""
        moment = self.now() if at is None else float(at)
        import datetime as _dt

        local = _dt.datetime.fromtimestamp(moment, tz=self._tz)
        return local.strftime("%Y%m%d")

    def isoformat(self, at: float | None = None) -> str:
        """本地时间字符串（日志与审计用）。"""
        moment = self.now() if at is None else float(at)
        import datetime as _dt

        return _dt.datetime.fromtimestamp(moment, tz=self._tz).isoformat(timespec="seconds")


class FakeClock(WorldClock):
    """测试用时钟：``advance(10 * 60)`` 直接跳过十分钟（§二十四）。"""

    def __init__(self, start: float = 1_700_000_000.0, timezone: str = DEFAULT_TIMEZONE) -> None:
        self._start = float(start)
        self._value = float(start)
        super().__init__(timezone)

    def now(self) -> float:
        return float(self._value)

    def advance(self, seconds: float) -> float:
        """把世界时间往前推（秒）。返回推进后的时间。"""
        self._value += float(seconds)
        return float(self._value)

    def advance_minutes(self, minutes: float) -> float:
        return self.advance(float(minutes) * 60.0)

    def advance_hours(self, hours: float) -> float:
        return self.advance(float(hours) * 3600.0)
