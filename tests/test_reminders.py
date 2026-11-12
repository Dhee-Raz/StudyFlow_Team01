"""Deadline worker and preferences tests: no real emails or provider credentials."""
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests
from flask import Flask
from sqlalchemy import update

from auth import auth
from models import db, User, Course, Assignment, StudySession, ReminderPreference, DeadlineNotification
from reminders import register_reminders, run_reminders, deadline_utc, DeliveryError, ResendEmail

NOW = datetime(2026, 11, 11, 12)


class FakeProvider:
    def __init__(self):
        self.calls = []
        self.accepted = {}
        self.fail = None

    def send(self, payload, key):
        self.calls.append((payload, key))
        self.accepted.setdefault(key, f'email-{len(self.accepted)}')
        if self.fail:
            raise self.fail
        return self.accepted[key]


@pytest.fixture
def app(tmp_path):
    app = Flask(__name__, template_folder=str(Path(__file__).resolve().parents[1] / 'templates'))
    app.config.update(TESTING=True, SECRET_KEY='test', SQLALCHEMY_DATABASE_URI=f'sqlite:///{tmp_path / "reminders.db"}')
    db.init_app(app)
    app.register_blueprint(auth)
    for name in ('index', 'add_course', 'add_assignment', 'calendar_view', 'schedule', 'study_availability', 'connect_canvas'):
        app.add_url_rule('/' + name, name, lambda: 'ok')
    register_reminders(app)
    with app.app_context():
        db.create_all()
        user = User(id=1, email='student@example.edu', password_hash='test-only')
        other = User(id=2, email='other@example.edu', password_hash='test-only')
        db.session.add_all([user, other]); db.session.flush()
        course = Course(id=1, user_id=1, name='Physics')
        db.session.add(course); db.session.flush()
        db.session.add(Assignment(id=1, course_id=1, title='Lab report', due_date=NOW + timedelta(hours=7), estimated_hours=4))
        db.session.add(ReminderPreference(user_id=1, enabled=True, hours='6,7', timezone='UTC'))
        db.session.commit()
    yield app
    with app.app_context():
        db.session.remove(); db.engine.dispose()


def run(provider, now=NOW):
    return run_reminders(now=now, provider=provider, sender='StudyFlow <reminders@example.edu>')


def test_six_and_seven_hour_offsets_and_no_duplicates(app):
    provider = FakeProvider()
    with app.app_context():
        assert run(provider, NOW - timedelta(seconds=1))['sent'] == 0
        assert run(provider)['sent'] == 1
        assert run(provider)['sent'] == 0
        assert run(provider, NOW + timedelta(hours=1))['sent'] == 1
        assert run(provider, NOW + timedelta(hours=1, minutes=1))['sent'] == 0
        assert len(provider.accepted) == 2
        payload = provider.calls[0][0]
        assert payload['to'] == ['student@example.edu']
        for value in ['Lab report', 'Physics', '2026-11-11', '07:00 PM', '4.00']:
            assert value in payload['text']
        assert DeadlineNotification.query.count() == 2


def test_completed_hours_stop_reminders_and_partial_hours_are_accurate(app):
    provider = FakeProvider()
    with app.app_context():
        db.session.add(StudySession(user_id=1, assignment_id=1, session_date=NOW.date(), duration_hours=1.5, completed=True))
        db.session.commit()
        run(provider)
        assert '2.50' in provider.calls[0][0]['text']
        db.session.add(StudySession(user_id=1, assignment_id=1, session_date=NOW.date(), duration_hours=2.5, completed=True))
        db.session.commit()
        assert run(provider, NOW + timedelta(hours=1))['sent'] == 0


@pytest.mark.parametrize('change', ['disabled', 'missing', 'past', 'zero', 'wrong_offset'])
def test_ineligible_assignments_are_not_sent(app, change):
    provider = FakeProvider()
    with app.app_context():
        preference = db.session.get(ReminderPreference, 1)
        assignment = db.session.get(Assignment, 1)
        if change == 'disabled': preference.enabled = False
        if change == 'missing': assignment.due_date = None
        if change == 'past': assignment.due_date = NOW - timedelta(hours=1)
        if change == 'zero': assignment.estimated_hours = 0
        if change == 'wrong_offset': preference.hours = '24'
        db.session.commit()
        run(provider)
        assert not provider.calls


def test_ambiguous_timeout_reuses_key_and_payload(app, caplog):
    provider = FakeProvider(); provider.fail = DeliveryError('timeout', True)
    with app.app_context():
        assert run(provider)['retry'] == 1
        first = DeadlineNotification.query.one()
        assert first.status == 'retry' and first.last_error == 'timeout'
        assert 'reminder_delivery_failed' in caplog.text
        provider.fail = None
        assert run(provider, NOW + timedelta(minutes=3))['sent'] == 1
        assert len(provider.calls) == 2 and len(provider.accepted) == 1
        assert provider.calls[0] == provider.calls[1]


@pytest.mark.parametrize('change', ['disabled', 'completed', 'deadline', 'title', 'email'])
def test_pending_retries_recheck_current_data(app, change):
    provider = FakeProvider(); provider.fail = DeliveryError('timeout', True)
    with app.app_context():
        run(provider)
        assignment = db.session.get(Assignment, 1)
        if change == 'disabled': db.session.get(ReminderPreference, 1).enabled = False
        if change == 'completed':
            db.session.add(StudySession(user_id=1, assignment_id=1, session_date=NOW.date(), duration_hours=4, completed=True))
        if change == 'deadline': assignment.due_date += timedelta(days=1)
        if change == 'title': assignment.title = 'Changed title'
        if change == 'email': db.session.get(User, 1).email = 'new@example.edu'
        db.session.commit(); provider.fail = None
        assert run(provider, NOW + timedelta(minutes=3))['cancelled'] == 1
        assert len(provider.calls) == 1


def test_active_lease_and_crash_recovery_do_not_duplicate(app):
    provider = FakeProvider()
    with app.app_context():
        run(provider)
        item = DeadlineNotification.query.one()
        # Simulate worker death after provider accepted but before final DB status.
        item.status = 'sending'; item.lease_until = NOW + timedelta(minutes=2)
        db.session.commit()
        assert run(provider, NOW + timedelta(minutes=1))['sent'] == 0
        assert run(provider, NOW + timedelta(minutes=3))['sent'] == 1
        assert len(provider.accepted) == 1


def test_no_retry_after_provider_idempotency_window(app):
    provider = FakeProvider(); provider.fail = DeliveryError('timeout', True)
    with app.app_context():
        db.session.get(ReminderPreference, 1).hours = '48'
        db.session.get(Assignment, 1).due_date = NOW + timedelta(hours=48)
        db.session.commit(); run(provider)
        provider.fail = None
        assert run(provider, NOW + timedelta(hours=23))['failed'] == 1
        assert len(provider.calls) == 1


def test_new_deadline_has_new_event_and_late_catchup_is_bounded(app):
    provider = FakeProvider()
    with app.app_context():
        run(provider)
        db.session.get(Assignment, 1).due_date += timedelta(days=1)
        db.session.commit()
        assert run(provider, NOW + timedelta(days=1, minutes=5))['sent'] == 1
        assert len(provider.accepted) == 2
        assert run(provider, NOW + timedelta(days=1, hours=1, minutes=16))['sent'] == 0


def test_timezone_conversion_uses_canvas_or_student_zone(app):
    with app.app_context():
        pref = db.session.get(ReminderPreference, 1)
        assignment = db.session.get(Assignment, 1)
        assignment.due_date = datetime(2026, 11, 11, 13)
        pref.timezone = 'America/Chicago'
        assert deadline_utc(assignment, pref) == datetime(2026, 11, 11, 19)
        assignment.canvas_timezone = 'America/New_York'
        assert deadline_utc(assignment, pref) == datetime(2026, 11, 11, 18)
        assignment.due_date = datetime(2026, 7, 1, 13)
        assert deadline_utc(assignment, pref) == datetime(2026, 7, 1, 17)


def test_preferences_login_csrf_validation_and_ownership(app):
    client = app.test_client()
    assert client.get('/reminders').status_code == 302
    with client.session_transaction() as state:
        state['user_id'] = 2; state['_csrf_token'] = 'csrf'
    page = client.get('/reminders')
    assert page.status_code == 200 and b'other@example.edu' in page.data
    assert client.post('/reminders', data={'enabled': 'on'}).status_code == 400
    for hours, zone in [([], 'UTC'), (['999'], 'UTC'), (['6'], 'bad-zone')]:
        assert client.post('/reminders', data={'_csrf_token': 'csrf', 'enabled': 'on', 'hours': hours, 'timezone': zone}).status_code == 400
    assert client.post('/reminders', data={'_csrf_token': 'csrf', 'enabled': 'on', 'hours': ['6', '7'], 'timezone': 'UTC', 'user_id': '1'}).status_code == 302
    with app.app_context():
        assert db.session.get(ReminderPreference, 2).enabled
        assert db.session.get(ReminderPreference, 1).timezone == 'UTC'
    assert client.post('/reminders', data={'_csrf_token': 'csrf', 'timezone': 'UTC'}).status_code == 302
    with app.app_context():
        assert not db.session.get(ReminderPreference, 2).enabled


def test_unconfigured_worker_fails_without_claiming_rows(app):
    app.config['RESEND_API_KEY'] = None
    with app.app_context():
        with pytest.raises(RuntimeError): run_reminders(now=NOW)
        assert DeadlineNotification.query.count() == 0
    result = app.test_cli_runner().invoke(args=['reminders'])
    assert result.exit_code != 0 and 'RESEND_API_KEY' in result.output


@pytest.mark.parametrize('status,retry', [(401, False), (422, False), (429, True), (500, True), (302, False)])
def test_provider_errors_are_sanitized(monkeypatch, status, retry):
    client = Mock(); client.__enter__ = Mock(return_value=client); client.__exit__ = Mock(return_value=False)
    client.post.return_value = Mock(status_code=status)
    monkeypatch.setattr('reminders.requests.Session', lambda: client)
    with pytest.raises(DeliveryError) as caught: ResendEmail('test-key').send({'text': 'private'}, 'stable-key')
    assert caught.value.retryable == retry
    assert client.post.call_args.kwargs['allow_redirects'] is False
    assert client.post.call_args.kwargs['headers']['Idempotency-Key'] == 'stable-key'


def test_database_creation_is_repeatable_and_keeps_ledger(app):
    with app.app_context():
        run(FakeProvider()); db.create_all(); db.create_all()
        assert DeadlineNotification.query.one().status == 'sent'
