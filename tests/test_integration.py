"""Exercise Ticket 8 through HTTP forms with an isolated SQLite database."""
import importlib
from datetime import date, datetime, timedelta, timezone

import pytest


@pytest.fixture(scope="module")
def planner(tmp_path_factory):
    database_path = tmp_path_factory.mktemp("studyflow-integration") / "test.db"
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("STUDYFLOW_DATABASE_URI", f"sqlite:///{database_path}")
        module = importlib.import_module("app")
        module.app.config.update(TESTING=True)
        yield module
        with module.app.app_context():
            module.db.session.remove()
            module.db.engine.dispose()


@pytest.fixture
def client(planner, monkeypatch):
    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 3)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls(2026, 10, 3, 12)
            return value.replace(tzinfo=timezone.utc) if tz else value

    monkeypatch.setattr(planner, "date", FixedDate)
    monkeypatch.setattr(planner, "datetime", FixedDateTime)
    monkeypatch.setattr("scheduler.datetime", FixedDateTime)

    with planner.app.app_context():
        planner.db.drop_all()
        planner.db.create_all()
    with planner.app.test_client() as browser:
        yield browser
    with planner.app.app_context():
        planner.db.session.remove()


def test_course_form_to_database_to_generated_schedule(client, planner):
    # One client follows the entire flow; no records are inserted directly.
    assert client.get("/").status_code == 200
    assert b"No upcoming study sessions" in client.get("/schedule").data

    course_page = client.post(
        "/add-course", data={"name": "CS 3398"}, follow_redirects=True
    )
    assert course_page.status_code == 200
    assert b"CS 3398" in course_page.data

    with planner.app.app_context():
        course = planner.Course.query.one()
        course_id = course.id

    assignment_form = client.get("/add-assignment")
    assert assignment_form.status_code == 200
    assert f'value="{course_id}"'.encode() in assignment_form.data
    assert b"CS 3398" in assignment_form.data

    response = client.post(
        "/add-assignment",
        data={
            "course_id": str(course_id),
            "title": "Sprint 1 Report",
            "due_date": "2026-10-10",
            "estimated_hours": "7",
        },
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/schedule")

    schedule = client.get(response.headers["Location"])
    assert schedule.status_code == 200
    html = schedule.get_data(as_text=True)
    assert html.count("<strong>Sprint 1 Report</strong>") == 7
    assert html.count("1.00 hours") == 7
    assert html.count("CS 3398") == 7
    dates = [
        (date(2026, 10, 3) + timedelta(days=offset)).strftime("%A, %B %d, %Y")
        for offset in range(7)
    ]
    positions = [html.index(day) for day in dates]
    assert positions == sorted(positions)
    assert html.count("<h2>") == 7

    # Data remains available on a new request and on the course listing.
    assert client.get("/schedule").data.count(b"Sprint 1 Report") == 7
    listing = client.get("/")
    assert b"Sprint 1 Report" in listing.data
    with planner.app.app_context():
        assignment = planner.Assignment.query.one()
        assert assignment.course_id == course_id
        assert assignment.estimated_hours == 7
        assert assignment.due_date.date() == date(2026, 10, 10)


def test_invalid_assignment_does_not_create_sessions(client, planner):
    client.post("/add-course", data={"name": "CS 3398"})
    with planner.app.app_context():
        course_id = planner.Course.query.one().id
    response = client.post(
        "/add-assignment",
        data={
            "course_id": str(course_id),
            "title": "",
            "due_date": "not-a-date",
            "estimated_hours": "-1",
        },
    )
    assert response.status_code == 400
    assert b"Enter an assignment title" in response.data
    assert b"Enter a valid due date" in response.data
    assert b"Estimated study hours must be greater than zero" in response.data
    with planner.app.app_context():
        assert planner.Assignment.query.count() == 0
    assert b"No upcoming study sessions" in client.get("/schedule").data


def test_assignment_without_course_guides_student_to_add_course(client, planner):
    form = client.get("/add-assignment")
    assert form.status_code == 200
    assert b"Add a course before creating an assignment" in form.data
    response = client.post("/add-assignment", data={}, follow_redirects=True)
    assert response.status_code == 200
    assert response.request.path == "/add-course"
    with planner.app.app_context():
        assert planner.Assignment.query.count() == 0
