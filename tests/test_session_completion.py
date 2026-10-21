"""SCRUM-27: session completion and progress."""
import importlib
from datetime import date, datetime, timedelta

import pytest


@pytest.fixture
def planner(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDYFLOW_DATABASE_URI", f"sqlite:///{tmp_path / 'test.db'}")
    module = importlib.reload(importlib.import_module("app"))
    module.app.config.update(TESTING=True)
    with module.app.app_context():
        module.db.drop_all()
        module.db.create_all()
    yield module
    with module.app.app_context():
        module.db.session.remove()
        module.db.engine.dispose()


def register(planner, email):
    browser = planner.app.test_client()
    browser.get("/register")
    with browser.session_transaction() as state:
        token = state["_csrf_token"]
    response = browser.post("/register", data={
        "_csrf_token": token, "email": email,
        "password": "test-password-1234", "confirm_password": "test-password-1234",
    })
    assert response.status_code == 302
    return browser


def post(browser, path, data=None, **kwargs):
    with browser.session_transaction() as state:
        token = state["_csrf_token"]
    return browser.post(path, data={"_csrf_token": token, **(data or {})}, **kwargs)


def add_assignment(planner, browser, hours=4, days_until_due=2):
    post(browser, "/add-course", data={"name": "CS 3398"})
    with planner.app.app_context():
        course_id = planner.Course.query.order_by(planner.Course.id.desc()).first().id
    due = (date.today() + timedelta(days=days_until_due)).isoformat()
    post(browser, "/add-assignment", data={
        "course_id": str(course_id), "title": "Sprint Report",
        "due_date": due, "estimated_hours": str(hours),
    })
    browser.get("/schedule")  # creates the saved sessions
    with planner.app.app_context():
        return [s.id for s in planner.StudySession.query.order_by(planner.StudySession.session_date)]


def session(planner, session_id):
    with planner.app.app_context():
        return planner.db.session.get(planner.StudySession, session_id)


def test_mark_complete_and_incomplete_persists(planner):
    owner = register(planner, "owner@example.edu")
    first, _ = add_assignment(planner, owner)

    assert post(owner, f"/sessions/{first}/toggle").status_code == 302
    saved = session(planner, first)
    assert saved.completed is True and saved.completed_at is not None

    post(owner, f"/sessions/{first}/toggle")
    saved = session(planner, first)
    assert saved.completed is False and saved.completed_at is None


def test_progress_shows_completed_against_estimated(planner):
    owner = register(planner, "owner@example.edu")
    first, _ = add_assignment(planner, owner, hours=4)

    html = owner.get("/schedule").get_data(as_text=True)
    assert "0.00 of 4.00 hrs completed (0%)" in html

    post(owner, f"/sessions/{first}/toggle")
    html = owner.get("/schedule").get_data(as_text=True)
    assert "2.00 of 4.00 hrs completed (50%)" in html
    assert "Completed" in html and "Upcoming" in html


def test_completed_session_survives_refresh(planner):
    owner = register(planner, "owner@example.edu")
    first, _ = add_assignment(planner, owner)
    post(owner, f"/sessions/{first}/toggle")
    owner.get("/schedule")
    owner.get("/schedule")
    assert session(planner, first).completed is True


def test_other_user_cannot_change_session(planner):
    owner = register(planner, "owner@example.edu")
    first, _ = add_assignment(planner, owner)

    intruder = register(planner, "intruder@example.edu")
    assert post(intruder, f"/sessions/{first}/toggle").status_code == 404
    assert session(planner, first).completed is False


def test_toggle_requires_login(planner):
    owner = register(planner, "owner@example.edu")
    first, _ = add_assignment(planner, owner)
    anonymous = planner.app.test_client()
    response = anonymous.post(f"/sessions/{first}/toggle")
    assert response.status_code in (302, 400, 403)
    assert session(planner, first).completed is False


def test_other_user_cannot_delete_assignment(planner):
    owner = register(planner, "owner@example.edu")
    add_assignment(planner, owner)
    with planner.app.app_context():
        assignment_id = planner.Assignment.query.one().id

    intruder = register(planner, "intruder@example.edu")
    response = post(intruder, f"/calendar/assignment/{assignment_id}/delete")
    assert response.status_code == 404
    with planner.app.app_context():
        assert planner.db.session.get(planner.Assignment, assignment_id) is not None
