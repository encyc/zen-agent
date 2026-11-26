# tests/test_automations_schedule.py
import time

import pytest

from zenagent.app.automations import Schedule


def at(y, mo, d, h, mi):
    return time.mktime((y, mo, d, h, mi, 0, 0, 0, -1))


def test_parse_daily_and_every():
    assert Schedule.parse("daily 09:00") == Schedule(kind="daily", at="09:00")
    assert Schedule.parse("every 15m") == Schedule(kind="every", seconds=900)
    assert Schedule.parse("every 2h") == Schedule(kind="every", seconds=7200)


@pytest.mark.parametrize("raw", ["", "daily", "daily 9", "hourly", "every 0m", "every 5s"])
def test_parse_rejects_unknown_forms(raw):
    with pytest.raises(ValueError):
        Schedule.parse(raw)


def test_daily_without_history_starts_at_next_occurrence():
    now = at(2026, 11, 26, 14, 0)          # 14:00, schedule is 09:00
    due = Schedule.parse("daily 09:00").next_due(now, None)
    assert due == at(2026, 11, 27, 9, 0)


def test_daily_rolls_over_midnight():
    now = at(2026, 11, 26, 23, 30)
    due = Schedule.parse("daily 00:10").next_due(now, None)
    assert due == at(2026, 11, 27, 0, 10)


def test_every_without_history_is_one_interval_out():
    now = at(2026, 11, 26, 9, 0)
    assert Schedule.parse("every 10m").next_due(now, None) == now + 600


def test_every_anchors_on_the_last_run_so_an_overdue_job_is_due():
    now = 1000.0
    # Overdue by 6 intervals: next_due stays in the past so due_list can fire.
    assert Schedule.parse("every 10m").next_due(now, 400.0) == 1000.0


def test_every_with_a_backwards_clock_waits_instead_of_bursting():
    now = 1000.0
    # A sleep/DST jump put the record an hour ahead. Never due before now, so
    # the job waits out the remainder of one interval instead of firing twice.
    assert Schedule.parse("every 10m").next_due(now, 4600.0) == 5200.0


def test_daily_history_makes_an_overdue_slot_due_now():
    # Ran yesterday 09:00; it is now 14:00 -> the 09:00 slot is overdue.
    now = at(2026, 11, 26, 14, 0)
    due = Schedule.parse("daily 09:00").next_due(now, at(2026, 11, 25, 9, 0))
    assert due == at(2026, 11, 26, 9, 0)


def test_daily_history_that_just_ran_is_not_due_again():
    # Ran today 03:00; it is now 05:00 -> the next slot is tomorrow 09:00.
    now = at(2026, 11, 26, 5, 0)
    due = Schedule.parse("daily 09:00").next_due(now, at(2026, 11, 26, 3, 0))
    assert due == at(2026, 11, 27, 9, 0)


def test_json_round_trip():
    for raw in ("daily 07:45", "every 90m"):
        parsed = Schedule.parse(raw)
        assert Schedule.from_json(parsed.to_json()) == parsed
