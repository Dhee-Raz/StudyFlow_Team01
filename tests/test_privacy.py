import os
import tempfile

# Must be set before importing app
_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["STUDYFLOW_DATABASE_URI"] = f"sqlite:///{_db.name}"

from datetime import datetime, timedelta
import pytest
from app import app
from models import db, User, Course, Assignment, PersonalTask

TOKEN = "test-token"


@pytest.fixture
def ids():
    app.config["TESTING"] = True
    with app.app_context():
        db.drop_all()
        db.create_all()
        a = User(email="a@test.com", password_hash="x")
        b = User(email="b@test.com", password_hash="x")
        db.session.add_all([a, b])
        db.session.commit()
        course = Course(name="A Secret Course", user_id=a.id)
        db.session.add(course)
        db.session.commit()
        due = datetime.now() + timedelta(days=5)
        assignment = Assignment(course=course, title="A Secret Assignment",
                                due_date=due, estimated_hours=2)
        task = PersonalTask(title="A Secret Task", due_date=due, user_id=a.id)
        db.session.add_all([assignment, task])
        db.session.commit()
        return dict(a=a.id, b=b.id, course=course.id,
                    assignment=assignment.id, task=task.id)


def client_for(user_id):
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = user_id
        s["_csrf_token"] = TOKEN
    return client


def post(client, url, **data):
    return client.post(url, data={"_csrf_token": TOKEN, **data})


def test_b_cannot_update_a_task(ids):
    r = post(client_for(ids["b"]), f"/calendar/personal/{ids['task']}",
             title="hacked", due_date="2030-01-01")
    assert r.status_code == 404
    with app.app_context():
        assert db.session.get(PersonalTask, ids["task"]).title == "A Secret Task"


def test_b_cannot_delete_a_task(ids):
    r = post(client_for(ids["b"]), f"/calendar/personal/{ids['task']}/delete")
    assert r.status_code == 404
    with app.app_context():
        assert db.session.get(PersonalTask, ids["task"]) is not None


def test_b_cannot_update_a_assignment(ids):
    r = post(client_for(ids["b"]), f"/calendar/assignment/{ids['assignment']}",
             title="hacked", due_date="2030-01-01", estimated_hours="3")
    assert r.status_code == 404
    with app.app_context():
        assert db.session.get(Assignment, ids["assignment"]).title == "A Secret Assignment"


def test_b_cannot_delete_a_assignment(ids):
    r = post(client_for(ids["b"]), f"/calendar/assignment/{ids['assignment']}/delete")
    assert r.status_code == 404
    with app.app_context():
        assert db.session.get(Assignment, ids["assignment"]) is not None


def test_b_cannot_delete_a_course(ids):
    r = post(client_for(ids["b"]), f"/delete-course/{ids['course']}")
    assert r.status_code == 404
    with app.app_context():
        assert db.session.get(Course, ids["course"]) is not None


def test_b_cannot_add_assignment_to_a_course(ids):
    with app.app_context():
        own = Course(name="B Course", user_id=ids["b"])
        db.session.add(own)
        db.session.commit()
        before = Assignment.query.count()
    due = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d")
    r = post(client_for(ids["b"]), "/add-assignment", course_id=ids["course"],
             title="sneaky", due_date=due, estimated_hours="2")
    assert r.status_code == 400
    with app.app_context():
        assert Assignment.query.count() == before


def test_b_pages_do_not_show_a_data(ids):
    c = client_for(ids["b"])
    for url in ("/", "/calendar", "/schedule"):
        html = c.get(url).get_data(as_text=True)
        assert "A Secret" not in html
