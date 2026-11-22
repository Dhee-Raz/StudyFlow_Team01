"""SCRUM-26: saved study sessions that respect availability."""
import importlib
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from scheduler import plan_sessions

TODAY = date(2026, 10, 19)


def make(id, days_until_due, hours):
    return SimpleNamespace(
        id=id,
        due_date=datetime.combine(TODAY + timedelta(days=days_until_due), datetime.min.time()),
        estimated_hours=hours,
    )


def hours_on(plan, day):
    return sum(h for (_, d), h in plan.items() if d == day)


# ----- planning logic (no database) -----------------------------------------

def test_never_exceeds_daily_capacity():
    plan, _ = plan_sessions([make(1, 3, 10), make(2, 3, 10)], TODAY, daily_capacity=4)
    for offset in range(3):
        assert hours_on(plan, TODAY + timedelta(days=offset)) <= 4 + 0.01


def test_earlier_deadline_gets_scheduled_first():
    early, late = make(1, 2, 8), make(2, 5, 8)
    plan, summaries = plan_sessions([late, early], TODAY, daily_capacity=4)
    assert summaries[1]["unscheduled"] == 0
    assert plan[(1, TODAY)] == 4
    assert (2, TODAY) not in plan


def test_reports_unscheduled_hours_when_too_much_work():
    _, summaries = plan_sessions([make(1, 2, 10)], TODAY, daily_capacity=4)
    assert summaries[1]["scheduled"] == 8
    assert summaries[1]["unscheduled"] == 2


def test_expired_deadline_gets_no_sessions():
    plan, summaries = plan_sessions([make(1, -1, 5)], TODAY)
    assert plan == {}
    assert summaries[1]["status"] == "overdue"
    assert summaries[1]["unscheduled"] == 5


def test_zero_remaining_work_gets_no_sessions():
    plan, summaries = plan_sessions(
        [make(1, 3, 4)], TODAY, completed_hours_by_assignment={1: 4}
    )
    assert plan == {}
    assert summaries[1]["status"] == "done"


def test_completed_hours_use_up_that_days_capacity():
    plan, _ = plan_sessions(
        [make(1, 1, 4)], TODAY, daily_capacity=4, completed_hours_by_day={TODAY: 3}
    )
    assert plan[(1, TODAY)] == 1


# ----- saved records (real database) ----------------------------------------

@pytest.fixture
def planner(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDYFLOW_DATABASE_URI", f"sqlite:///{tmp_path / 'test.db'}")
    module = importlib.reload(importlib.import_module("app"))
    with module.app.app_context():
        module.db.drop_all()
        module.db.create_all()
        yield module
        module.db.session.remove()
        module.db.engine.dispose()


def seed(planner, hours=6, days_until_due=3):
    from models import User
    user = User(email="student@example.edu", password_hash="x")
    course = planner.Course(name="CS 3398", owner=user)
    assignment = planner.Assignment(
        course=course,
        title="Sprint Report",
        due_date=datetime.combine(TODAY + timedelta(days=days_until_due), datetime.min.time()),
        estimated_hours=hours,
    )
    planner.db.session.add_all([user, course, assignment])
    planner.db.session.commit()
    return user, assignment


def saved(planner, user):
    return planner.StudySession.query.filter_by(user_id=user.id).all()


def test_creates_persistent_records(planner):
    from scheduler import refresh_saved_sessions
    user, assignment = seed(planner)
    refresh_saved_sessions(planner.db, user.id, TODAY)
    sessions = saved(planner, user)
    assert len(sessions) == 3
    for session in sessions:
        assert session.assignment_id == assignment.id
        assert session.session_date >= TODAY
        assert session.duration_hours == 2
        assert session.completed is False


def test_refresh_does_not_duplicate(planner):
    from scheduler import refresh_saved_sessions
    user, _ = seed(planner)
    refresh_saved_sessions(planner.db, user.id, TODAY)
    first_ids = sorted(s.id for s in saved(planner, user))
    refresh_saved_sessions(planner.db, user.id, TODAY)
    refresh_saved_sessions(planner.db, user.id, TODAY)
    assert sorted(s.id for s in saved(planner, user)) == first_ids


def test_regeneration_keeps_completed_sessions(planner):
    from scheduler import refresh_saved_sessions
    user, assignment = seed(planner)
    refresh_saved_sessions(planner.db, user.id, TODAY)
    done = planner.StudySession.query.filter_by(session_date=TODAY).one()
    done.completed = True
    planner.db.session.commit()

    # Change the estimate and regenerate the next day.
    assignment.estimated_hours = 8
    planner.db.session.commit()
    refresh_saved_sessions(planner.db, user.id, TODAY + timedelta(days=1))

    sessions = saved(planner, user)
    kept = [s for s in sessions if s.completed]
    assert len(kept) == 1 and kept[0].id == done.id and kept[0].duration_hours == 2
    # Remaining 6 hours spread over the 2 days left.
    upcoming = [s for s in sessions if not s.completed]
    assert sum(s.duration_hours for s in upcoming) == 6


def test_deleting_assignment_deletes_its_sessions(planner):
    from scheduler import refresh_saved_sessions
    user, assignment = seed(planner)
    refresh_saved_sessions(planner.db, user.id, TODAY)
    planner.db.session.delete(assignment)
    planner.db.session.commit()
    assert saved(planner, user) == []

# ----- automatic rescheduling of missed sessions (SCRUM-35) -----------------

def test_missed_session_hours_are_rescheduled_on_remaining_days(planner):
    from scheduler import refresh_saved_sessions

    user, assignment = seed(planner, hours=6, days_until_due=3)
    refresh_saved_sessions(planner.db, user.id, TODAY)
    original = saved(planner, user)
    assert len(original) == 3
    assert sorted(s.duration_hours for s in original) == [2.0, 2.0, 2.0]

    # The student misses the first session. Refreshing tomorrow must keep all
    # unfinished hours but schedule them only on the remaining available days.
    tomorrow = TODAY + timedelta(days=1)
    refresh_saved_sessions(planner.db, user.id, tomorrow)
    rescheduled = saved(planner, user)
    assert len(rescheduled) == 2
    assert sorted(s.session_date for s in rescheduled) == [
        tomorrow, tomorrow + timedelta(days=1)
    ]
    assert sum(s.duration_hours for s in rescheduled) == pytest.approx(6.0)
    assert all(
        not s.completed and s.status == "scheduled" and s.completed_hours == 0
        for s in rescheduled
    )

    # Repeated page refreshes must not create duplicate sessions.
    ids = sorted(s.id for s in rescheduled)
    refresh_saved_sessions(planner.db, user.id, tomorrow)
    assert sorted(s.id for s in saved(planner, user)) == ids


def test_missed_session_rescheduling_respects_daily_capacity(planner):
    from scheduler import refresh_saved_sessions

    user, assignment = seed(planner, hours=10, days_until_due=3)
    refresh_saved_sessions(planner.db, user.id, TODAY, daily_capacity=4)
    tomorrow = TODAY + timedelta(days=1)
    summaries = refresh_saved_sessions(
        planner.db, user.id, tomorrow, daily_capacity=4
    )

    upcoming = saved(planner, user)
    assert len(upcoming) == 2
    assert all(s.session_date >= tomorrow for s in upcoming)
    assert sum(s.duration_hours for s in upcoming) == pytest.approx(8.0)
    assert summaries[assignment.id]["unscheduled"] == pytest.approx(2.0)
