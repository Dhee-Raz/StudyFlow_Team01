"""Ticket 9: verify Canvas credentials; never import planner records."""
import re
import secrets
import time
from threading import Lock
from urllib.parse import urlsplit

import requests
from flask import current_app, flash, redirect, render_template, request, session, url_for


class CanvasConnectionError(ValueError):
    pass


def normalize_canvas_url(value):
    value = value.strip()
    if '://' not in value:
        value = 'https://' + value
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ''
        invalid = (parsed.scheme != 'https' or parsed.username or parsed.password
                   or parsed.port not in (None, 443) or parsed.path not in ('', '/')
                   or parsed.query or parsed.fragment)
    except ValueError:
        invalid = True
        host = ''
    allowed = current_app.config.get('CANVAS_ALLOWED_HOSTS', ())
    if (invalid or not re.fullmatch(r'[a-z0-9-]+(?:\.[a-z0-9-]+)+', host)
            or not (host.endswith('.instructure.com') or host in allowed)):
        raise CanvasConnectionError('Enter your HTTPS Canvas school address, such as https://school.instructure.com. For a custom school domain, ask the StudyFlow administrator to enable it.')
    return 'https://' + host


def verify_canvas(base_url, token):
    if not token or len(token) > 4096 or any(char.isspace() for char in token):
        raise CanvasConnectionError('Enter a valid personal access token.')
    try:
        # No redirects: never forward credentials to another destination.
        with requests.Session() as http:
            http.trust_env = False
            response = http.get(
                base_url + '/api/v1/users/self/profile',
                headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/json'},
                timeout=(5, 15), allow_redirects=False,
            )
        if response.status_code in (401, 403):
            raise CanvasConnectionError('Canvas rejected the token. Check that it is valid, has not expired, and belongs to this school.')
        if response.status_code == 429:
            raise CanvasConnectionError('Canvas is busy. Wait a moment and try again.')
        if response.status_code != 200:
            raise CanvasConnectionError('Canvas could not verify the connection. Check the school address and try again.')
        profile = response.json()
        if not isinstance(profile, dict) or not profile.get('id') or not isinstance(profile.get('name'), str):
            raise CanvasConnectionError('Canvas returned an unexpected response. Check the school address.')
        return {'id': profile['id'], 'name': profile['name'], 'time_zone': profile.get('time_zone')}
    except (requests.RequestException, ValueError) as error:
        if isinstance(error, CanvasConnectionError):
            raise
        # Do not log exceptions: HTTP exception objects can contain credentials.
        raise CanvasConnectionError('Could not reach Canvas securely. Check your connection and try again.') from None


class ConnectionStore:
    """Ephemeral credentials keyed by an opaque browser identifier, never a cookie token."""
    def __init__(self):
        self.records = {}
        self.lock = Lock()

    def get(self, key):
        with self.lock:
            for expired in [k for k, v in self.records.items() if v['expires'] <= time.monotonic()]:
                del self.records[expired]
            return self.records.get(key)

    def save(self, old_key, base_url, token, profile):
        self.get(old_key)  # Prune expired connections.
        key = secrets.token_urlsafe(32)
        with self.lock:
            self.records.pop(old_key, None)
            self.records[key] = dict(url=base_url, token=token, profile=profile,
                                     expires=time.monotonic() + 3600)
        return key

    def remove(self, key):
        with self.lock:
            self.records.pop(key, None)


def register_canvas(app):
    store = ConnectionStore()
    app.extensions['canvas_connections'] = store

    def csrf_valid():
        expected = session.get('canvas_csrf', '')
        actual = request.form.get('csrf_token', '')
        return bool(expected and secrets.compare_digest(expected, actual))

    def page(error=None, status=200):
        session.setdefault('canvas_csrf', secrets.token_urlsafe(32))
        record = store.get(session.get('canvas_connection'))
        # Deliberately pass only safe display fields to Jinja.
        connection = {'url': record['url'], 'name': record['profile']['name']} if record else None
        return render_template('connect_canvas.html', connection=connection,
                               csrf_token=session['canvas_csrf'], error=error), status

    @app.route('/connect-canvas', methods=['GET', 'POST'])
    def connect_canvas():
        request.max_content_length = 16384
        if request.method == 'GET':
            return page()
        if not csrf_valid():
            return page('Your form expired. Reload this page and try again.', 400)
        try:
            base_url = normalize_canvas_url(request.form.get('canvas_url', ''))
            token = request.form.get('access_token', '').strip()
            profile = verify_canvas(base_url, token)
        except CanvasConnectionError as error:
            return page(str(error), 400)
        session['canvas_connection'] = store.save(session.get('canvas_connection'), base_url, token, profile)
        session['canvas_csrf'] = secrets.token_urlsafe(32)
        flash('Canvas connection verified. You can now import your courses and assignments.')
        return redirect(url_for('connect_canvas'))

    @app.route('/disconnect-canvas', methods=['POST'])
    def disconnect_canvas():
        if not csrf_valid():
            return page('Your form expired. Reload this page and try again.', 400)
        store.remove(session.pop('canvas_connection', None))
        session['canvas_csrf'] = secrets.token_urlsafe(32)
        flash('Disconnected from Canvas. Your planner data has not changed.')
        return redirect(url_for('connect_canvas'))

    @app.route('/sync-canvas', methods=['POST'], endpoint='sync_canvas')
    @app.route('/import-canvas', methods=['POST'])
    def import_canvas():
        from canvas_import import CanvasImportError, import_courses
        if not csrf_valid():
            return page('Your form expired. Reload this page and try again.', 400)
        record = store.get(session.get('canvas_connection'))
        if not record:
            return page('Connect to Canvas before importing. Your previous connection may have expired.', 400)
        try:
            from auth import current_user
            counts = import_courses(record, current_user().id, sync=request.endpoint == "sync_canvas")
        except CanvasImportError as error:
            return page(str(error), 400)
        flash(f"{'Sync' if request.endpoint == 'sync_canvas' else 'Import'} complete: {counts['courses']} courses added, "
              f"{counts['assignments']} assignments added, {counts['updated']} items updated, {counts['skipped']} items skipped, "
              f"{counts['failed']} invalid items failed. "
              f"{counts['missing_deadlines']} assignments need a deadline. {counts['missing']} previously imported assignments were not returned by Canvas and were kept for review. "
              "Imported assignments start with an editable 2-hour study estimate.")
        return redirect(url_for('index'))

    @app.after_request
    def protect_canvas_response(response):
        if request.endpoint in ('connect_canvas', 'disconnect_canvas', 'import_canvas', 'sync_canvas'):
            response.headers['Cache-Control'] = 'no-store'
            response.headers['Referrer-Policy'] = 'no-referrer'
        return response
