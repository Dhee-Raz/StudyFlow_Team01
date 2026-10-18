"""Sprint 2: account authentication and cross-account access tests."""
import importlib

import pytest
from werkzeug.security import check_password_hash


@pytest.fixture
def planner(tmp_path, monkeypatch):
    # CI executes this file separately to isolate the Flask app module import.
    monkeypatch.setenv("STUDYFLOW_DATABASE_URI", f"sqlite:///{tmp_path / 'auth.db'}")
    app_module = importlib.import_module("app")
    app_module.app.config["TESTING"] = True
    with app_module.app.app_context():
        app_module.db.drop_all()
        app_module.db.create_all()
    yield app_module
    with app_module.app.app_context():
        app_module.db.session.remove()


def token(client):
    with client.session_transaction() as state:
        return state["_csrf_token"]


def submit(client, route, data=None):
    return client.post(route, data={"_csrf_token": token(client), **(data or {})})


def register(client, email, password="correct-password-123"):
    client.get("/register")
    return submit(client, "/register", {
        "email": email, "password": password, "confirm_password": password
    })


def test_registration_hashes_password_and_normalizes_email(planner):
    with planner.app.test_client() as browser:
        response = register(browser, "  STUDENT@EXAMPLE.EDU  ")
        assert response.status_code == 302
        assert browser.get("/").status_code == 200
    with planner.app.app_context():
        user = planner.db.session.query(planner.db.Model.registry._class_registry["User"]).one()
        assert user.email == "student@example.edu"
        assert user.password_hash != "correct-password-123"
        assert check_password_hash(user.password_hash, "correct-password-123")


def test_duplicate_email_bad_password_and_logout(planner):
    with planner.app.test_client() as browser:
        assert register(browser, "student@example.edu").status_code == 302
        assert submit(browser, "/logout").status_code == 302
        assert browser.get("/").status_code == 302
        assert register(browser, "STUDENT@EXAMPLE.EDU").status_code == 400
        assert submit(browser, "/login", {
            "email": "student@example.edu", "password": "incorrect"
        }).status_code == 401
        assert submit(browser, "/login", {
            "email": " STUDENT@EXAMPLE.EDU ", "password": "correct-password-123"
        }).status_code == 302
        assert submit(browser, "/logout").status_code == 302
        assert browser.get("/schedule").status_code == 302


def test_csrf_rejects_missing_tokens(planner):
    with planner.app.test_client() as browser:
        browser.get("/register")
        assert browser.post("/register", data={
            "email": "x@example.edu", "password": "correct-password-123",
            "confirm_password": "correct-password-123"
        }).status_code == 400
        assert register(browser, "x@example.edu").status_code == 302
        assert browser.post("/add-course", data={"name": "bad"}).status_code == 400
        assert browser.post("/logout").status_code == 400


def test_user_isolation_for_planner_records(planner):
    with planner.app.test_client() as alice, planner.app.test_client() as bob:
        register(alice, "alice@example.edu")
        assert submit(alice, "/add-course", {"name": "Alice only"}).status_code == 302
        with planner.app.app_context():
            course = planner.Course.query.one()
            alice_course_id = course.id

        register(bob, "bob@example.edu")
        assert b"Alice only" not in bob.get("/").data
        assert submit(bob, "/delete-course/" + str(alice_course_id)).status_code == 404
        assert submit(bob, "/add-assignment", {
            "course_id": str(alice_course_id), "title": "Illegal",
            "due_date": "2099-01-01", "estimated_hours": "2"
        }).status_code == 302  # Redirects to add-course when no owned course exists.
        assert submit(bob, "/add-course", {"name": "Bob only"}).status_code == 302
        response = submit(bob, "/add-assignment", {
            "course_id": str(alice_course_id), "title": "Illegal",
            "due_date": "2099-01-01", "estimated_hours": "2"
        })
        assert response.status_code == 400
        assert b"Alice only" in alice.get("/").data
        assert b"Bob only" not in alice.get("/").data
