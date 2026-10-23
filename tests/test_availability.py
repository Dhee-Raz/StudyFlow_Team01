"""Sprint 2 Ticket 4: availability validation, persistence and planner scheduling."""
import importlib
import sqlite3
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from scheduler import plan_sessions
from migrations import upgrade_study_availability


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


def csrf(client):
    with client.session_transaction() as state:
        return state["_csrf_token"]


def post(client, url, data):
    return client.post(url, data={"_csrf_token": csrf(client), **data})


def register(app, email):
    browser = app.test_client()
    browser.get("/register")
    result = post(browser, "/register", {
        "email": email, "password": "safe-test-password-123",
        "confirm_password": "safe-test-password-123",
    })
    assert result.status_code == 302
    return browser


def test_defaults_validation_and_account_isolation(planner):
    a = register(planner.app, "first@example.edu")
    b = register(planner.app, "second@example.edu")
    response = a.get("/availability")
    assert response.status_code == 200 and b"4.0" in response.data
    bad = post(a, "/availability", {"daily_study_hours": "5"})
    assert bad.status_code == 400
    bad = post(a, "/availability", {"weekdays": ["0", "1"], "daily_study_hours": "0"})
    assert bad.status_code == 400
    bad = post(a, "/availability", {"weekdays": ["0", "1"], "daily_study_hours": "nan"})
    assert bad.status_code == 400
    bad = post(a, "/availability", {"weekdays": ["0", "9"], "daily_study_hours": "2"})
    assert bad.status_code == 400
    good = post(a, "/availability", {"weekdays": ["0", "2", "4"], "daily_study_hours": "2.5"})
    assert good.status_code == 302
    assert b"value=\"2.5\"" in a.get("/availability").data
    with planner.app.app_context():
        from models import User
        assert User.query.filter_by(email="first@example.edu").one().available_weekdays == "0,2,4"
        assert User.query.filter_by(email="second@example.edu").one().daily_study_hours == 4.0
    assert b"value=\"4.0\"" in b.get("/availability").data
    assert post(a, "/logout", {}).status_code == 302
    assert a.get("/availability").status_code == 302
    a.get("/login")
    logged = post(a, "/login", {"email": "first@example.edu", "password": "safe-test-password-123"})
    assert logged.status_code == 302
    assert b"value=\"2.5\"" in a.get("/availability").data


def test_weekdays_and_capacity_in_pure_scheduler():
    today = date(2026, 10, 26)  # Monday
    item = SimpleNamespace(id=1, due_date=datetime(2026, 11, 2),
                           estimated_hours=12)
    plan, summary = plan_sessions([item], today, daily_capacity=2,
                                  available_weekdays={0, 2, 4})
    assert {day.weekday() for _, day in plan} == {0, 2, 4}
    assert sum(plan.values()) == 6
    assert summary[1]["unscheduled"] == 6
    with pytest.raises(ValueError):
        plan_sessions([item], today, available_weekdays=set())


def test_custom_settings_change_schedule_and_preserve_completed(planner):
    a = register(planner.app, "schedule@example.edu")
    post(a, "/add-course", {"name": "Computer Science"})
    with planner.app.app_context():
        course = planner.Course.query.one()
        course_id = course.id
    due = date.today() + timedelta(days=14)
    post(a, "/add-assignment", {
        "course_id": str(course_id), "title": "Study report",
        "due_date": due.isoformat(), "estimated_hours": "8",
    })
    allowed = str(date.today().weekday())
    assert post(a, "/availability", {
        "weekdays": [allowed], "daily_study_hours": "1"
    }).status_code == 302
    view = a.get("/schedule")
    assert view.status_code == 200
    with planner.app.app_context():
        rows = planner.StudySession.query.all()
        assert rows
        assert all(s.session_date.weekday() == date.today().weekday() for s in rows)
        assert all(s.duration_hours <= 1.0 for s in rows)
        kept_id = rows[0].id
        rows[0].completed = True
        planner.db.session.commit()
    a.get("/schedule")
    with planner.app.app_context():
        kept = planner.db.session.get(planner.StudySession, kept_id)
        assert kept.completed is True


def test_upgrade_existing_user_table_is_idempotent(tmp_path):
    from sqlalchemy import create_engine, text
    database = tmp_path / "old.db"
    with sqlite3.connect(database) as con:
        con.execute("CREATE TABLE user (id INTEGER PRIMARY KEY, email TEXT, password_hash TEXT, created_at DATETIME)")
        con.execute("INSERT INTO user (id,email,password_hash,created_at) VALUES (7,'old@example.edu','hash','2026-10-01')")
    engine = create_engine(f"sqlite:///{database}")
    upgrade_study_availability(engine)
    upgrade_study_availability(engine)
    with engine.connect() as connection:
        result = connection.execute(text("SELECT id,email,available_weekdays,daily_study_hours FROM user")).one()
        assert result == (7, "old@example.edu", "0,1,2,3,4,5,6", 4.0)
    engine.dispose()
