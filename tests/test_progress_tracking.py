"""Sprint 3: assignment progress tracking.

Acceptance criteria covered:
- Students can update assignment status (Not Started / In Progress / Completed).
- Completed and remaining hours are displayed.
- Progress updates correctly when work is recorded.
- Students can adjust remaining estimates.
- Completed assignments are excluded from future scheduling.
- Progress information persists after logging out.
"""
import importlib
import sqlite3
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine

from migrations import upgrade_assignment_progress
from scheduler import plan_sessions

PASSWORD = "test-password-1234"


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


def _token(browser):
    with browser.session_transaction() as state:
        if "_csrf_token" not in state:
            browser.get("/login")
    with browser.session_transaction() as state:
        return state["_csrf_token"]


def post(browser, path, data=None, **kwargs):
    return browser.post(path, data={"_csrf_token": _token(browser), **(data or {})}, **kwargs)


def register(planner, email):
    browser = planner.app.test_client()
    browser.get("/register")
    response = post(browser, "/register", data={
        "email": email, "password": PASSWORD, "confirm_password": PASSWORD,
    })
    assert response.status_code == 302
    return browser


def add_assignment(planner, browser, title="Sprint Report", hours=4, days_until_due=2):
    post(browser, "/add-course", data={"name": "CS 3398"})
    with planner.app.app_context():
        course_id = planner.Course.query.order_by(planner.Course.id.desc()).first().id
    post(browser, "/add-assignment", data={
        "course_id": str(course_id), "title": title,
        "due_date": (date.today() + timedelta(days=days_until_due)).isoformat(),
        "estimated_hours": str(hours),
    })
    browser.get("/schedule")  # builds saved sessions
    with planner.app.app_context():
        return planner.Assignment.query.filter_by(title=title).one().id


def assignment(planner, assignment_id):
    with planner.app.app_context():
        item = planner.db.session.get(planner.Assignment, assignment_id)
        return SimpleNamespace(status=item.status, estimated_hours=item.estimated_hours,
                               completed_hours=item.completed_hours,
                               remaining_hours=item.remaining_hours,
                               progress_percent=item.progress_percent)


def sessions(planner, assignment_id):
    with planner.app.app_context():
        return [SimpleNamespace(id=s.id, completed=s.completed, hours=s.duration_hours,
                                day=s.session_date)
                for s in planner.StudySession.query.filter_by(assignment_id=assignment_id)
                .order_by(planner.StudySession.session_date)]


# --- statuses ---------------------------------------------------------------

def test_new_assignment_starts_not_started_and_shows_status(planner):
    owner = register(planner, "owner@example.edu")
    item = add_assignment(planner, owner)
    assert assignment(planner, item).status == "not_started"
    assert "Not Started" in owner.get("/schedule").get_data(as_text=True)
    assert "Not Started" in owner.get("/").get_data(as_text=True)


@pytest.mark.parametrize("status,label", [
    ("in_progress", "In Progress"), ("completed", "Completed"), ("not_started", "Not Started"),
])
def test_student_can_update_status(planner, status, label):
    owner = register(planner, "owner@example.edu")
    item = add_assignment(planner, owner)
    response = post(owner, f"/assignments/{item}/status", data={"status": status})
    assert response.status_code == 302 and response.headers["Location"].endswith("/schedule")
    assert assignment(planner, item).status == status
    assert f'status-pill status-{status}">{label}' in owner.get("/schedule").get_data(as_text=True)


def test_invalid_status_is_rejected(planner):
    owner = register(planner, "owner@example.edu")
    item = add_assignment(planner, owner)
    page = post(owner, f"/assignments/{item}/status", data={"status": "done-ish"},
                follow_redirects=True).get_data(as_text=True)
    assert "Choose Not Started, In Progress, or Completed." in page
    assert assignment(planner, item).status == "not_started"


def test_status_can_return_to_courses_page(planner):
    owner = register(planner, "owner@example.edu")
    item = add_assignment(planner, owner)
    response = post(owner, f"/assignments/{item}/status",
                    data={"status": "in_progress", "return_to": "index"})
    assert response.headers["Location"].endswith("/")
    # Unknown targets fall back to the schedule (no open redirect).
    response = post(owner, f"/assignments/{item}/status",
                    data={"status": "in_progress", "return_to": "https://evil.example"})
    assert response.headers["Location"].endswith("/schedule")


def test_other_users_cannot_change_progress(planner):
    owner = register(planner, "owner@example.edu")
    item = add_assignment(planner, owner)
    intruder = register(planner, "intruder@example.edu")
    assert post(intruder, f"/assignments/{item}/status", data={"status": "completed"}).status_code == 404
    assert post(intruder, f"/assignments/{item}/remaining", data={"remaining_hours": "9"}).status_code == 404
    after = assignment(planner, item)
    assert after.status == "not_started" and after.estimated_hours == 4


def test_progress_routes_require_login(planner):
    owner = register(planner, "owner@example.edu")
    item = add_assignment(planner, owner)
    anonymous = planner.app.test_client()
    assert anonymous.post(f"/assignments/{item}/status",
                          data={"status": "completed"}).status_code in (302, 400, 403)
    assert assignment(planner, item).status == "not_started"


# --- hours recorded / remaining ---------------------------------------------

def test_completing_a_session_records_hours_and_starts_assignment(planner):
    owner = register(planner, "owner@example.edu")
    item = add_assignment(planner, owner, hours=4, days_until_due=2)
    first = sessions(planner, item)[0]
    assert first.hours == 2.0

    post(owner, f"/sessions/{first.id}/toggle")
    after = assignment(planner, item)
    assert after.status == "in_progress"
    assert (after.completed_hours, after.remaining_hours, after.progress_percent) == (2.0, 2.0, 50)

    html = owner.get("/schedule").get_data(as_text=True)
    assert "2.00 of 4.00 hrs completed (50%) &middot; 2.00 hrs remaining" in html
    assert "<strong>2 hrs</strong> remaining" in html

    # Undo removes the recorded hours again.
    post(owner, f"/sessions/{first.id}/toggle")
    after = assignment(planner, item)
    assert (after.completed_hours, after.remaining_hours) == (0.0, 4.0)


def test_courses_page_shows_completed_and_remaining_hours(planner):
    owner = register(planner, "owner@example.edu")
    item = add_assignment(planner, owner, hours=4, days_until_due=2)
    post(owner, f"/sessions/{sessions(planner, item)[0].id}/toggle")
    html = owner.get("/").get_data(as_text=True)
    assert "2 of 4 hrs done" in html and "2 hrs left" in html
    assert 'value="50"' in html


# --- adjusting remaining estimates -------------------------------------------

def test_student_can_adjust_remaining_estimate(planner):
    owner = register(planner, "owner@example.edu")
    item = add_assignment(planner, owner, hours=4, days_until_due=3)
    post(owner, f"/sessions/{sessions(planner, item)[0].id}/toggle")  # ~1.33 hrs done
    done = assignment(planner, item).completed_hours

    post(owner, f"/assignments/{item}/remaining", data={"remaining_hours": "6"})
    after = assignment(planner, item)
    assert after.remaining_hours == 6.0
    assert after.estimated_hours == round(done + 6, 2)

    owner.get("/schedule")  # rescheduling picks up the new estimate
    upcoming = [s for s in sessions(planner, item) if not s.completed]
    assert sum(s.hours for s in upcoming) == pytest.approx(6.0, abs=0.02)


@pytest.mark.parametrize("value", ["-1", "abc", "nan", "inf", "", "5000"])
def test_invalid_remaining_estimates_are_rejected(planner, value):
    owner = register(planner, "owner@example.edu")
    item = add_assignment(planner, owner, hours=4)
    page = post(owner, f"/assignments/{item}/remaining", data={"remaining_hours": value},
                follow_redirects=True).get_data(as_text=True)
    assert "Enter remaining hours between 0 and 1000." in page
    assert assignment(planner, item).estimated_hours == 4


def test_zero_remaining_without_recorded_work_is_rejected(planner):
    owner = register(planner, "owner@example.edu")
    item = add_assignment(planner, owner, hours=4)
    post(owner, f"/assignments/{item}/remaining", data={"remaining_hours": "0"})
    assert assignment(planner, item).estimated_hours == 4


def test_adding_hours_to_completed_assignment_resumes_it(planner):
    owner = register(planner, "owner@example.edu")
    item = add_assignment(planner, owner, hours=4)
    post(owner, f"/assignments/{item}/status", data={"status": "completed"})
    post(owner, f"/assignments/{item}/remaining", data={"remaining_hours": "3"})
    assert assignment(planner, item).status == "in_progress"
    owner.get("/schedule")
    assert round(sum(s.hours for s in sessions(planner, item)), 2) == 3.0


# --- scheduling integration --------------------------------------------------

def test_completed_assignment_is_excluded_from_scheduling(planner):
    owner = register(planner, "owner@example.edu")
    item = add_assignment(planner, owner, hours=6, days_until_due=3)
    other = add_assignment(planner, owner, title="Lab 4", hours=2, days_until_due=3)
    first = sessions(planner, item)[0]
    post(owner, f"/sessions/{first.id}/toggle")

    post(owner, f"/assignments/{item}/status", data={"status": "completed"})
    html = owner.get("/schedule").get_data(as_text=True)

    remaining_sessions = sessions(planner, item)
    # Recorded work is kept; every upcoming session is removed.
    assert [s.id for s in remaining_sessions] == [first.id]
    assert remaining_sessions[0].completed is True
    assert sessions(planner, other)  # other work is still scheduled
    assert assignment(planner, item).remaining_hours == 0
    assert assignment(planner, item).progress_percent == 100
    assert "1 of 2 completed" in html

    # Moving it back to In Progress puts the remaining work back on the schedule.
    post(owner, f"/assignments/{item}/status", data={"status": "in_progress"})
    owner.get("/schedule")
    upcoming = [s for s in sessions(planner, item) if not s.completed]
    assert sum(s.hours for s in upcoming) == pytest.approx(4.0, abs=0.02)


def test_completed_assignment_frees_capacity_for_others(planner):
    owner = register(planner, "owner@example.edu")
    post(owner, "/availability", data={"weekdays": [str(d) for d in range(7)],
                                       "daily_study_hours": "2"})
    big = add_assignment(planner, owner, title="Big", hours=4, days_until_due=2)
    later = add_assignment(planner, owner, title="Later", hours=4, days_until_due=2)
    html = owner.get("/schedule").get_data(as_text=True)
    assert "Not enough available study time" in html

    post(owner, f"/assignments/{big}/status", data={"status": "completed"})
    html = owner.get("/schedule").get_data(as_text=True)
    assert "Not enough available study time" not in html
    assert round(sum(s.hours for s in sessions(planner, later)), 2) == 4.0


def test_plan_sessions_skips_completed_assignments():
    today = date(2026, 11, 16)
    done = SimpleNamespace(id=1, due_date=datetime(2026, 11, 18), estimated_hours=4,
                           status="completed")
    active = SimpleNamespace(id=2, due_date=datetime(2026, 11, 18), estimated_hours=4,
                             status="in_progress")
    plan, summaries = plan_sessions([done, active], today)
    assert all(assignment_id == 2 for assignment_id, _ in plan)
    assert summaries[1] == {"remaining": 0.0, "scheduled": 0.0, "unscheduled": 0.0,
                            "status": "completed"}
    assert summaries[2]["scheduled"] == 4.0


# --- persistence --------------------------------------------------------------

def test_progress_persists_after_logout(planner):
    owner = register(planner, "owner@example.edu")
    item = add_assignment(planner, owner, hours=4, days_until_due=2)
    post(owner, f"/sessions/{sessions(planner, item)[0].id}/toggle")
    post(owner, f"/assignments/{item}/remaining", data={"remaining_hours": "5"})

    post(owner, "/logout")
    assert owner.get("/schedule").status_code == 302

    fresh = planner.app.test_client()
    fresh.get("/login")
    post(fresh, "/login", data={"email": "owner@example.edu", "password": PASSWORD})
    html = fresh.get("/schedule").get_data(as_text=True)
    assert 'status-pill status-in_progress">In Progress' in html
    assert "2.00 of 7.00 hrs completed (29%) &middot; 5.00 hrs remaining" in html


def test_migration_adds_status_and_backfills_in_progress(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as raw:
        raw.executescript("""
            CREATE TABLE assignment (id INTEGER PRIMARY KEY, title VARCHAR(200));
            CREATE TABLE study_session (id INTEGER PRIMARY KEY, assignment_id INTEGER,
                                        completed BOOLEAN);
            INSERT INTO assignment VALUES (1, 'Started'), (2, 'Untouched');
            INSERT INTO study_session VALUES (1, 1, 1), (2, 2, 0);
        """)
    engine = create_engine(f"sqlite:///{path}")
    upgrade_assignment_progress(engine)
    upgrade_assignment_progress(engine)  # idempotent
    engine.dispose()
    with sqlite3.connect(path) as raw:
        rows = raw.execute("SELECT id, status FROM assignment ORDER BY id").fetchall()
    assert rows == [(1, "in_progress"), (2, "not_started")]
