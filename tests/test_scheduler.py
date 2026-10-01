from datetime import date, datetime
from types import SimpleNamespace

from scheduler import generate_study_sessions

TODAY = date(2026, 10, 1)


def make_assignment(due, hours):
    # A simple stand-in for the real Assignment model
    return SimpleNamespace(due_date=due, estimated_hours=hours)


def test_hours_add_up_and_dates_are_in_range():
    a = make_assignment(datetime(2026, 10, 4), 5)  # 3 days away
    sessions = generate_study_sessions(a, today=TODAY)
    assert sum(s["duration"] for s in sessions) == 5
    assert all(TODAY <= s["date"] < date(2026, 10, 4) for s in sessions)
    assert all(s["assignment"] is a for s in sessions)


def test_close_deadline_compresses_but_still_generates():
    a = make_assignment(datetime(2026, 10, 2), 4)  # due tomorrow
    sessions = generate_study_sessions(a, today=TODAY)
    assert len(sessions) == 1
    assert sessions[0]["duration"] == 4


def test_few_hours_many_days_does_not_make_tiny_sessions():
    a = make_assignment(datetime(2026, 10, 11), 2)  # 10 days, 2 hours
    sessions = generate_study_sessions(a, today=TODAY)
    assert sum(s["duration"] for s in sessions) == 2
    assert all(s["duration"] >= 0.5 for s in sessions)


def test_zero_hours_returns_empty():
    a = make_assignment(datetime(2026, 10, 5), 0)
    assert generate_study_sessions(a, today=TODAY) == []


def test_past_due_date_returns_empty():
    a = make_assignment(datetime(2026, 9, 20), 3)
    assert generate_study_sessions(a, today=TODAY) == []


def test_missing_due_date_returns_empty():
    a = make_assignment(None, 3)
    assert generate_study_sessions(a, today=TODAY) == []
