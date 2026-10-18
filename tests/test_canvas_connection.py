"""Canvas connection tests use fake HTTP responses, never personal tokens or live APIs."""
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests
from flask import Flask, Blueprint

from canvas_connection import register_canvas


@pytest.fixture
def site(monkeypatch):
    app = Flask(__name__, template_folder=str(Path(__file__).resolve().parents[1] / 'templates'))
    app.config.update(TESTING=True, SECRET_KEY='test-secret')
    for endpoint in ('index', 'add_course', 'add_assignment', 'calendar_view', 'schedule'):
        app.add_url_rule('/' + endpoint, endpoint, lambda: '')
    auth = Blueprint('auth', __name__)
    auth.add_url_rule('/login', endpoint='login', view_func=lambda: '')
    auth.add_url_rule('/register', endpoint='register', view_func=lambda: '')
    auth.add_url_rule('/logout', endpoint='logout', view_func=lambda: '')
    app.register_blueprint(auth)
    app.context_processor(lambda: {'form_csrf_token': lambda: 'standalone-test-token'})
    register_canvas(app)
    response = Mock(status_code=200)
    response.json.return_value = {'id': 42, 'name': 'Test Student'}
    http = Mock()
    http.__enter__ = Mock(return_value=http)
    http.__exit__ = Mock(return_value=False)
    http.get.return_value = response
    monkeypatch.setattr('canvas_connection.requests.Session', lambda: http)
    return app, http, response


def submit(client, **values):
    client.get('/connect-canvas')
    with client.session_transaction() as session:
        csrf = session['canvas_csrf']
    return client.post('/connect-canvas', data=dict(
        csrf_token=csrf, canvas_url=values.get('url', 'https://school.instructure.com'),
        access_token=values.get('token', 'private-test-token')), follow_redirects=True)


def test_connect_private_isolated_and_disconnect(site):
    app, http, _ = site
    client = app.test_client()
    result = submit(client)
    assert b'Connected as Test Student' in result.data
    assert b'private-test-token' not in result.data
    assert result.headers['Cache-Control'] == 'no-store'
    with client.session_transaction() as session:
        assert 'private-test-token' not in str(dict(session))
        csrf = session['canvas_csrf']
    args, kwargs = http.get.call_args
    assert args == ('https://school.instructure.com/api/v1/users/self/profile',)
    assert kwargs['headers']['Authorization'] == 'Bearer private-test-token'
    assert kwargs['allow_redirects'] is False
    assert kwargs['timeout'] == (5, 15)
    assert b'Connected as' not in app.test_client().get('/connect-canvas').data
    assert client.post('/disconnect-canvas', data={}).status_code == 400
    result = client.post('/disconnect-canvas', data={'csrf_token': csrf}, follow_redirects=True)
    assert b'Connected as' not in result.data
    assert app.extensions['canvas_connections'].records == {}


@pytest.mark.parametrize('url', ['http://school.instructure.com', 'https://evil.example',
    'https://school.instructure.com.evil.example', 'https://localhost', 'https://127.0.0.1',
    'https://user:pass@school.instructure.com', 'https://school.instructure.com/path',
    'https://school.instructure.com?token=secret', 'https://school.instructure.com:444', 'https://[invalid'])
def test_bad_hosts_never_receive_token(site, url):
    app, http, _ = site
    assert submit(app.test_client(), url=url).status_code == 400
    http.get.assert_not_called()


@pytest.mark.parametrize('status', [401, 403, 429, 500, 302])
def test_failed_verification_does_not_store_token(site, status):
    app, _, response = site
    response.status_code = status
    result = submit(app.test_client())
    assert result.status_code == 400
    assert b'private-test-token' not in result.data
    assert not app.extensions['canvas_connections'].records


@pytest.mark.parametrize('failure', [requests.Timeout('secret'), requests.ConnectionError('secret'), ValueError('secret')])
def test_network_and_json_errors_are_safe(site, failure):
    app, http, response = site
    if isinstance(failure, requests.RequestException):
        http.get.side_effect = failure
    else:
        response.json.side_effect = failure
    result = submit(app.test_client())
    assert result.status_code == 400
    assert b'secret' not in result.data


def test_malformed_profile_and_blank_token(site):
    app, http, response = site
    response.json.return_value = []
    assert submit(app.test_client()).status_code == 400
    http.get.reset_mock()
    assert submit(app.test_client(), token='').status_code == 400
    http.get.assert_not_called()


def test_expiry_and_failed_reconnect(site):
    app, _, response = site
    client = app.test_client()
    submit(client)
    store = app.extensions['canvas_connections']
    response.status_code = 401
    assert submit(client).status_code == 400
    assert b'Connected as Test Student' in client.get('/connect-canvas').data
    for record in store.records.values():
        record['expires'] = 0
    assert b'Connected as' not in client.get('/connect-canvas').data
    assert not store.records


def test_csrf_and_custom_domain(site):
    app, http, _ = site
    client = app.test_client()
    assert client.post('/connect-canvas', data={'access_token': 'secret'}).status_code == 400
    http.get.assert_not_called()
    app.config['CANVAS_ALLOWED_HOSTS'] = ('canvas.school.edu',)
    assert submit(client, url='https://canvas.school.edu').status_code == 200
