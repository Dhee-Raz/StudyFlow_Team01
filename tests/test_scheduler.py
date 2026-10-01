from datetime import datetime, timedelta
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