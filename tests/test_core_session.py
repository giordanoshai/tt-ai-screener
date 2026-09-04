# -*- coding: utf-8 -*-
"""core/session.py 的测试。

时段判断是纯粹的日期时间算术，但**必须测冬夏令时**——美股三个时段的
北京时间边界在 11 月第一个周日之后整体顺延 1 小时，写死会导致每年
有半年时间盯错。

所有测试都传显式的 UTC 时刻，不依赖运行机器的时区。
"""
import datetime

from core import session

UTC = datetime.timezone.utc


def _utc(text):
    """'2026-09-04 01:00' -> 带 UTC 时区的 datetime。"""
    return datetime.datetime.fromisoformat(text).replace(tzinfo=UTC)


class TestDaylightSaving:
    def test_summer_is_dst(self):
        assert session.us_is_dst(datetime.date(2026, 9, 4)) is True

    def test_winter_is_not_dst(self):
        assert session.us_is_dst(datetime.date(2026, 12, 15)) is False

    def test_dst_starts_second_sunday_of_march(self):
        # 2026-03-08 是三月第二个周日
        assert session.us_is_dst(datetime.date(2026, 3, 7)) is False
        assert session.us_is_dst(datetime.date(2026, 3, 8)) is True

    def test_dst_ends_first_sunday_of_november(self):
        # 2026-11-01 是十一月第一个周日
        assert session.us_is_dst(datetime.date(2026, 10, 31)) is True
        assert session.us_is_dst(datetime.date(2026, 11, 1)) is False


class TestCnSession:
    def test_morning_open(self):
        # 北京 09:30 = UTC 01:30，2026-09-04 是周五
        assert "cn" in session.current(_utc("2026-09-04 01:30"))

    def test_lunch_break_is_closed(self):
        # 北京 12:00 = UTC 04:00，午休
        assert "cn" not in session.current(_utc("2026-09-04 04:00"))

    def test_afternoon_boundary(self):
        # 北京 14:59 = UTC 06:59 还在，15:01 = UTC 07:01 收了
        assert "cn" in session.current(_utc("2026-09-04 06:59"))
        assert "cn" not in session.current(_utc("2026-09-04 07:01"))

    def test_weekend_is_closed(self):
        # 2026-09-05 是周六，北京 10:00 = UTC 02:00
        assert session.current(_utc("2026-09-05 02:00")) == []


class TestUsSessions:
    def test_premarket_summer(self):
        """夏令时：盘前 = 美东 04:00-09:30 = 北京 16:00-21:30 = UTC 08:00-13:30。"""
        assert "us_pre" in session.current(_utc("2026-09-04 09:00"))

    def test_regular_hours_summer(self):
        """夏令时：常规 = 美东 09:30-16:00 = UTC 13:30-20:00。"""
        assert "us_rth" in session.current(_utc("2026-09-04 15:00"))

    def test_after_hours_summer(self):
        """夏令时：盘后 = 美东 16:00-20:00 = UTC 20:00-24:00。"""
        assert "us_post" in session.current(_utc("2026-09-04 21:00"))

    def test_winter_shifts_one_hour(self):
        """冬令时整体顺延 1 小时：同一个 UTC 时刻，夏天是常规，冬天还是盘前。"""
        assert "us_rth" in session.current(_utc("2026-09-04 14:00"))
        assert "us_pre" in session.current(_utc("2026-12-15 14:00"))


class TestConfirmTicks:
    def test_premarket_needs_more_confirmation(self):
        """盘前成交量只占全天 1%~2%，几百股就能把价格打出去，
        所以要更多采样点确认才算数。"""
        assert session.confirm_ticks("us_pre") == 3
        assert session.confirm_ticks("us_rth") == 2
        assert session.confirm_ticks("cn") == 2

    def test_is_premarket(self):
        assert session.is_premarket("us_pre") is True
        assert session.is_premarket("us_rth") is False
