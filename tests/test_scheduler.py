from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from scheduler import generate_study_sessions


def make_assignment(days_until_due, estimated_hours):
    return SimpleNamespace(
        id=1,
        title="CS Assignment",
        due_date=datetime.now() + timedelta(days=days_until_due),
        estimated_hours=estimated_hours,
    )


def test_generates_study_sessions():
    assignment = make_assignment(4, 8)

    sessions = generate_study_sessions(assignment)

    assert len(sessions) == 4
    assert sum(session["duration"] for session in sessions) == 8


def test_session_contains_required_information():
    assignment = make_assignment(3, 6)

    sessions = generate_study_sessions(assignment)

    for session in sessions:
        assert "date" in session
        assert "duration" in session
        assert "assignment" in session
        assert session["assignment"] is assignment


def test_zero_estimated_hours():
    assignment = make_assignment(5, 0)

    sessions = generate_study_sessions(assignment)

    assert sessions == []


def test_past_due_date():
    assignment = make_assignment(-2, 5)

    sessions = generate_study_sessions(assignment)

    assert sessions == []


def test_due_today_still_generates_session():
    assignment = make_assignment(0, 3)

    sessions = generate_study_sessions(assignment)

    assert len(sessions) == 1
    assert sessions[0]["duration"] == 3


def test_sessions_cover_available_days_and_split_fractional_hours(monkeypatch):
    class FrozenDateTime:
        @classmethod
        def now(cls, tz=None):
            fixed_now = datetime(2026, 10, 3, 12)
            return fixed_now.replace(tzinfo=timezone.utc) if tz else fixed_now

    monkeypatch.setattr("scheduler.datetime", FrozenDateTime)

    for due_date in (
        datetime(2026, 10, 6, 23, 59),
        datetime(2026, 10, 6, 23, 59, tzinfo=timezone.utc),
    ):
        assignment = SimpleNamespace(
            due_date=due_date,
            estimated_hours=7.5,
        )

        sessions = generate_study_sessions(assignment)

        assert [session["date"] for session in sessions] == [
            date(2026, 10, 3),
            date(2026, 10, 4),
            date(2026, 10, 5),
        ]
        assert [session["duration"] for session in sessions] == [2.5, 2.5, 2.5]
