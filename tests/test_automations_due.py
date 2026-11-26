# tests/test_automations_due.py
import time

from zenagent.app.automations import (
    Job,
    JobState,
    Schedule,
    due_list,
    wrap_prompt,
)


def at(y, mo, d, h, mi):
    return time.mktime((y, mo, d, h, mi, 0, 0, 0, -1))


def job(name="r", schedule="daily 09:00", **extra):
    return Job(
        id="a_1",
        name=name,
        prompt="跑取数脚本",
        schedule=Schedule.parse(schedule),
        session="s.jsonl",
        **extra,
    )


def test_job_without_history_is_not_due():
    now = at(2026, 11, 26, 23, 0)
    assert due_list([job()], {}, now) == []


def test_one_missed_round_is_due_with_missed_zero():
    j = job("r", "every 1h")
    now = at(2026, 11, 26, 11, 0)
    states = {"a_1": JobState(last_run_at=at(2026, 11, 26, 9, 0))}
    due = due_list([j], states, now)
    assert [d.job.name for d in due] == ["r"]
    assert due[0].missed == 1
    assert due[0].run_number == 1


def test_many_missed_rounds_report_the_count():
    j = job("r", "every 1h")
    states = {"a_1": JobState(last_run_at=at(2026, 11, 26, 6, 0), run_count=3)}
    due = due_list([j], states, at(2026, 11, 26, 11, 0))
    assert due[0].missed == 4
    assert due[0].run_number == 4


def test_disabled_and_future_jobs_are_skipped():
    now = at(2026, 11, 26, 11, 0)
    states = {"a_1": JobState(last_run_at=at(2026, 11, 26, 9, 0))}
    assert due_list([job(enabled=False)], states, now) == []


def test_due_list_is_ordered_by_due_time():
    a = job("a", "every 1h")
    b = Job(
        id="a_2",
        name="b",
        prompt="x",
        schedule=Schedule.parse("every 10m"),
        session="t.jsonl",
    )
    states = {
        "a_1": JobState(last_run_at=at(2026, 11, 26, 9, 0)),
        "a_2": JobState(last_run_at=at(2026, 11, 26, 10, 50)),
    }
    now = at(2026, 11, 26, 11, 30)
    # a came due at 10:00, b at 11:00: the most overdue runs first.
    assert [d.job.name for d in due_list([a, b], states, now)] == ["a", "b"]


def test_wrap_prompt_states_the_unattended_context_and_keeps_the_original():
    text = wrap_prompt(job(), run_number=3, now=at(2026, 11, 27, 9, 0))
    assert "第 3 次" in text
    assert "2026-11-27 09:00" in text
    assert "[automation] r" in text
    assert "ask" in text                     # tells the model nobody can answer
    assert "跑取数脚本" in text               # the user's prompt, verbatim
