"""Ticket 10 tests use isolated SQLite and mocked Canvas responses."""
import importlib
from datetime import datetime
from unittest.mock import Mock

import pytest
import requests
from sqlalchemy import create_engine, text

from canvas_import import CanvasImportError, import_courses
from migrations import upgrade_canvas_import
from models import db, Course, Assignment, User


@pytest.fixture
def planner(tmp_path, monkeypatch):
    monkeypatch.setenv('STUDYFLOW_DATABASE_URI', f'sqlite:///{tmp_path / "planner.db"}')
    module = importlib.import_module('app')
    module.app.config['TESTING'] = True
    with module.app.app_context():
        db.session.remove()
        db.drop_all()
        db.create_all()
        upgrade_canvas_import(db.engine)
        db.session.add(User(id=1, email="import@example.edu", password_hash="test-only"))
        db.session.commit()
        yield module.app
        db.session.rollback()
        db.session.remove()


@pytest.fixture
def record():
    return dict(url='https://school.instructure.com', token='private-test-token',
                profile=dict(id=12, time_zone='America/Chicago'))


def response(items, next_url=None, status=200):
    result = Mock(status_code=status)
    result.json.return_value = items
    result.links = {'next': {'url': next_url}} if next_url else {}
    return result


def course(cid=1):
    return dict(id=cid, name='Canvas Course', workflow_state='available')


def assignment(aid=10, **extra):
    return dict(id=aid, name='Canvas Essay', due_at='2026-10-21T01:00:00Z',
                points_possible=100, **extra)


@pytest.fixture
def http(monkeypatch):
    client = Mock()
    client.headers = {}
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=False)
    monkeypatch.setattr('canvas_import.requests.Session', lambda: client)
    return client


def test_import_timezone_points_links_and_duplicates(planner, record, http):
    http.get.side_effect = [response([course()]), response([assignment()])] * 2
    first = import_courses(record, 1)
    assert first['courses'] == first['assignments'] == 1
    item = Assignment.query.one()
    assert item.course == Course.query.one()
    assert item.due_date == datetime(2026, 10, 20, 20)
    assert item.canvas_timezone == 'America/Chicago'
    assert item.points_possible == 100
    assert item.estimated_hours == 2
    assert item.canvas_url == record['url'] + '/courses/1/assignments/10'
    item.estimated_hours = 4
    item.title = 'Local edit'
    db.session.commit()
    second = import_courses(record, 1)
    assert second['courses'] == second['assignments'] == 0
    assert second['skipped'] == 2
    assert Assignment.query.one().title == 'Local edit'
    assert Assignment.query.one().estimated_hours == 4
    assert http.headers['Authorization'] == 'Bearer private-test-token'
    assert all(call.kwargs['allow_redirects'] is False for call in http.get.call_args_list)
    with planner.test_client() as client:
        with client.session_transaction() as state:
            state['user_id'] = 1
            state['_csrf_token'] = 'test-csrf'

        html = client.get('/').data
        assert b'100.0 points' in html and b'Open in Canvas' in html
        assert b'private-test-token' not in html


def test_missing_deadline_can_be_reviewed_without_schedule(planner, record, http):
    item = assignment(); item['due_at'] = None
    http.get.side_effect = [response([course()]), response([item])]
    result = import_courses(record, 1)
    saved = Assignment.query.one()
    assert saved.due_date is None and result['missing_deadlines'] == 1
    with planner.test_client() as client:
        with client.session_transaction() as state:
            state['user_id'] = 1
            state['_csrf_token'] = 'test-csrf'

        assert b'No deadline' in client.get('/').data
        assert client.get('/schedule').status_code == 200
        page = client.get(f'/calendar?selected=assignment-{saved.id}')
        assert page.status_code == 200 and b'Canvas Essay' in page.data
        assert client.post(f'/calendar/assignment/{saved.id}', data={
            '_csrf_token': 'test-csrf', 'title': saved.title, 'due_date': '2026-12-20', 'estimated_hours': '2'}).status_code == 302
    assert saved.estimated_hours == 2 and saved.due_date is not None


def test_pagination_for_courses_and_assignments(planner, record, http):
    http.get.side_effect = [
        response([course()], record['url'] + '/api/v1/courses?page=2'),
        response([assignment()], record['url'] + '/api/v1/courses/1/assignments?page=2'),
        response([assignment(11)]), response([course(2)]), response([]),
    ]
    result = import_courses(record, 1)
    assert result['courses'] == result['assignments'] == 2


@pytest.mark.parametrize('next_url', ['https://evil.example/api/v1/courses/1/assignments?page=2',
    'http://school.instructure.com/api/v1/courses/1/assignments?page=2',
    'https://school.instructure.com/not-api',
    'https://user:pass@school.instructure.com/api/v1/courses/1/assignments?page=2'])
def test_unsafe_pagination_aborts_without_saving(planner, record, http, next_url):
    http.get.side_effect = [response([course()]), response([assignment()], next_url)]
    with pytest.raises(CanvasImportError):
        import_courses(record, 1)
    assert http.get.call_count == 2
    assert Course.query.count() == Assignment.query.count() == 0


@pytest.mark.parametrize('failure', [requests.Timeout('private-test-token'),
    response([], status=401), response([], status=403), response([], status=429),
    response([], status=500), response([], status=302), response({'bad': 'response'})])
def test_failed_pages_keep_existing_planner(planner, record, http, failure):
    db.session.add(Course(name='Keep me', user_id=1)); db.session.commit()
    http.get.side_effect = [response([course()]), failure]
    with pytest.raises(CanvasImportError) as error:
        import_courses(record, 1)
    assert 'private-test-token' not in str(error.value)
    assert Course.query.one().name == 'Keep me'
    assert Assignment.query.count() == 0


def test_empty_and_malformed_records(planner, record, http):
    http.get.side_effect = [response([])]
    assert import_courses(record, 1)['courses'] == 0
    item = assignment(); item['due_at'] = 'invalid'
    http.get.side_effect = [response([{}, course()]), response([{}, item, assignment()])]
    result = import_courses(record, 1)
    assert result['failed'] == 3 and result['assignments'] == 1


def test_remote_ids_do_not_collide_between_accounts(planner, record, http):
    http.get.side_effect = [response([course()]), response([assignment()])] * 3
    import_courses(record, 1)
    record['profile']['id'] = 13
    import_courses(record, 1)
    record['url'] = 'https://other.instructure.com'
    import_courses(record, 1)
    assert Course.query.count() == Assignment.query.count() == 3


def test_database_failure_rolls_back(planner, record, http, monkeypatch):
    from sqlalchemy.exc import IntegrityError
    http.get.side_effect = [response([course()]), response([assignment()])]
    monkeypatch.setattr(db.session, 'commit', Mock(side_effect=IntegrityError('test', {}, Exception())))
    with pytest.raises(CanvasImportError):
        import_courses(record, 1)
    assert Course.query.count() == Assignment.query.count() == 0


def test_import_route_requires_connection_and_csrf(planner, record, http):
    with planner.test_client() as client:
        with client.session_transaction() as state:
            state['user_id'] = 1
            state['_csrf_token'] = 'test-csrf'

        assert client.get('/import-canvas').status_code == 405
        assert client.post('/import-canvas').status_code == 400
        client.get('/connect-canvas')
        with client.session_transaction() as sess:
            csrf = sess['canvas_csrf']
        assert client.post('/import-canvas', data={'csrf_token': csrf, '_csrf_token': 'test-csrf'}).status_code == 400
        store = planner.extensions['canvas_connections']
        key = store.save(None, record['url'], record['token'], dict(record['profile'], name='Student'))
        with client.session_transaction() as sess:
            sess['canvas_connection'] = key
        http.get.side_effect = [response([course()]), response([assignment()])]
        result = client.post('/import-canvas', data={'csrf_token': csrf, '_csrf_token': 'test-csrf'}, follow_redirects=True)
        assert result.status_code == 200
        assert b'1 courses added' in result.data and b'1 assignments added' in result.data
        assert b'private-test-token' not in result.data


def test_old_database_migrates_twice_without_data_loss(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "old.db"}')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE course (id INTEGER PRIMARY KEY, name VARCHAR(100) NOT NULL)'))
        conn.execute(text('CREATE TABLE assignment (id INTEGER PRIMARY KEY, course_id INTEGER NOT NULL REFERENCES course(id), title VARCHAR(200) NOT NULL, due_date DATETIME NOT NULL, estimated_hours FLOAT NOT NULL)'))
        conn.execute(text("INSERT INTO course VALUES (1, 'Existing course')"))
        conn.execute(text("INSERT INTO assignment VALUES (1, 1, 'Existing task', '2026-12-20 00:00:00', 3)"))
    upgrade_canvas_import(engine)
    upgrade_canvas_import(engine)
    with engine.begin() as conn:
        assert conn.execute(text('SELECT title, estimated_hours FROM assignment')).one() == ('Existing task', 3)
        conn.execute(text('UPDATE assignment SET due_date=NULL'))
        assert not conn.execute(text('PRAGMA foreign_key_check')).all()
        assert any(row[1] == 'ux_assignment_canvas_key' for row in conn.execute(text('PRAGMA index_list(assignment)')))
    engine.dispose()


def test_edit_hours_preserves_imported_deadline_time(planner, record, http):
    http.get.side_effect = [response([course()]), response([assignment()])]
    import_courses(record, 1)
    item = Assignment.query.one()
    expected = item.due_date
    with planner.test_client() as client:
        with client.session_transaction() as state:
            state['user_id'] = 1
            state['_csrf_token'] = 'test-csrf'

        result = client.post(f'/calendar/assignment/{item.id}', data={
            '_csrf_token': 'test-csrf', 'title': item.title, 'due_date': expected.date().isoformat(), 'estimated_hours': '3'})
        assert result.status_code == 302
    assert item.due_date == expected and item.estimated_hours == 3


def test_repeated_page_is_rejected(planner, record, http):
    url = record['url'] + '/api/v1/courses?enrollment_type=student&enrollment_state=active&per_page=100'
    http.get.side_effect = [response([], url)]
    with pytest.raises(CanvasImportError):
        import_courses(record, 1)
    assert http.get.call_count == 1


def test_unique_source_key_constraint(planner, record, http):
    from sqlalchemy.exc import IntegrityError
    http.get.side_effect = [response([course()]), response([assignment()])]
    import_courses(record, 1)
    original = Course.query.one()
    db.session.add(Course(name='Duplicate', canvas_key=original.canvas_key, user_id=1))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()
    assert Course.query.count() == 1


def test_same_canvas_account_is_separate_for_two_studyflow_users(planner, record, http):
    db.session.add(User(id=2, email='second@example.edu', password_hash='test-only'))
    db.session.commit()
    http.get.side_effect = [response([course()]), response([assignment()])] * 2
    import_courses(record, 1)
    import_courses(record, 2)
    assert Course.query.filter_by(user_id=1).count() == 1
    assert Course.query.filter_by(user_id=2).count() == 1
    assert Assignment.query.count() == 2


def test_personalized_heading_and_authenticated_import_form(planner, record):
    client = planner.test_client()
    with client.session_transaction() as state:
        state['user_id'] = 1
        state['_csrf_token'] = 'test-csrf'
        state['canvas_connection'] = planner.extensions['canvas_connections'].save(
            None, record['url'], record['token'], dict(record['profile'], name='Roshan'))
    assert b'Roshan' in client.get('/').data
    response = client.get('/connect-canvas')
    assert response.status_code == 200
    assert b'Import Courses and Assignments' in response.data
    assert b'private-test-token' not in response.data
