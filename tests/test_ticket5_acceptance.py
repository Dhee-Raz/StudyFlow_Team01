"""Ticket 5 acceptance: saved sessions honor the authenticated user's availability."""
import importlib
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from scheduler import plan_sessions


@pytest.fixture
def planner(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDYFLOW_DATABASE_URI", f"sqlite:///{tmp_path / 'planner.db'}")
    module = importlib.reload(importlib.import_module("app"))
    module.app.config.update(TESTING=True)
    with module.app.app_context():
        module.db.drop_all()
        module.db.create_all()
    yield module
    with module.app.app_context():
        module.db.session.remove()
        module.db.engine.dispose()


def post(client, path, values=None):
    with client.session_transaction() as session:
        csrf = session["_csrf_token"]
    return client.post(path, data={"_csrf_token": csrf, **(values or {})})


def signup(app, email):
    client = app.test_client()
    client.get("/register")
    response = post(client, "/register", {
        "email": email, "password": "sample-secure-password-123",
        "confirm_password": "sample-secure-password-123",
    })
    assert response.status_code == 302
    return client


def course_and_assignment(planner, client, name, hours, due_in):
    assert post(client, "/add-course", {"name": name}).status_code == 302
    with planner.app.app_context():
        course_id = planner.Course.query.filter_by(name=name).one().id
    result = post(client, "/add-assignment", {
        "course_id": str(course_id),
        "title": f"{name} project",
        "due_date": (date.today() + timedelta(days=due_in)).isoformat(),
        "estimated_hours": str(hours),
    })
    assert result.status_code == 302


def test_capacity_shortfall_visible_to_student(planner):
    client = signup(planner.app, "capacity@example.edu")
    course_and_assignment(planner, client, "CS", hours=50, due_in=3)
    response = post(client, "/availability", {
        "weekdays": [str(i) for i in range(7)], "daily_study_hours": "2",
    })
    assert response.status_code == 302
    html = client.get("/schedule").get_data(as_text=True)
    assert "Not enough available study time" in html
    assert "44.00 hours not scheduled" in html
    with planner.app.app_context():
        rows = planner.StudySession.query.all()
        assert len(rows) == 3
        assert all(row.duration_hours == 2 for row in rows)


def test_per_user_preferences_do_not_mix_saved_sessions(planner):
    alice = signup(planner.app, "alice@example.edu")
    bob = signup(planner.app, "bob@example.edu")
    course_and_assignment(planner, alice, "Alice", hours=12, due_in=7)
    course_and_assignment(planner, bob, "Bob", hours=12, due_in=7)
    allowed = str(date.today().weekday())
    assert post(alice, "/availability", {
        "weekdays": [allowed], "daily_study_hours": "1",
    }).status_code == 302
    assert post(bob, "/availability", {
        "weekdays": [str(i) for i in range(7)], "daily_study_hours": "3",
    }).status_code == 302
    assert "Alice project" in alice.get("/schedule").get_data(as_text=True)
    assert "Bob project" not in alice.get("/schedule").get_data(as_text=True)
    assert "Bob project" in bob.get("/schedule").get_data(as_text=True)
    assert "Alice project" not in bob.get("/schedule").get_data(as_text=True)
    with planner.app.app_context():
        from models import User, StudySession
        users = {u.email: u.id for u in User.query.all()}
        a_rows = StudySession.query.filter_by(user_id=users["alice@example.edu"]).all()
        b_rows = StudySession.query.filter_by(user_id=users["bob@example.edu"]).all()
        assert a_rows and b_rows
        assert {r.session_date.weekday() for r in a_rows} == {date.today().weekday()}
        assert all(r.duration_hours <= 1 for r in a_rows)
        assert all(r.duration_hours <= 3 for r in b_rows)


def test_reschedule_preserves_completed_and_does_not_duplicate(planner):
    student = signup(planner.app, "reschedule@example.edu")
    course_and_assignment(planner, student, "Math", hours=8, due_in=7)
    assert student.get("/schedule").status_code == 200
    with planner.app.app_context():
        first = planner.StudySession.query.order_by(planner.StudySession.id).first()
        first_id = first.id
    assert post(student, f"/sessions/{first_id}/toggle").status_code == 302
    assert post(student, "/availability", {
        "weekdays": [str(i) for i in range(7)], "daily_study_hours": "1",
    }).status_code == 302
    for _ in range(3):
        assert student.get("/schedule").status_code == 200
    with planner.app.app_context():
        rows = planner.StudySession.query.all()
        kept = planner.db.session.get(planner.StudySession, first_id)
        assert kept.completed
        assert kept.duration_hours > 0
        uncompleted = [r for r in rows if not r.completed]
        assert len({(r.assignment_id, r.session_date) for r in uncompleted}) == len(uncompleted)
        assert all(r.duration_hours <= 1 for r in uncompleted)


def test_no_deadline_does_not_break_scheduler():
    today = date(2026, 10, 23)
    a = SimpleNamespace(id=1, due_date=None, estimated_hours=2)
    b = SimpleNamespace(id=2, due_date=datetime(2026, 10, 25), estimated_hours=2)
    planned, summaries = plan_sessions([a, b], today)
    assert 1 not in summaries
    assert 2 in summaries
    assert planned
