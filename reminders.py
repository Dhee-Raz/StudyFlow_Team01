"""Opt-in deadline reminders with a durable ledger and bounded provider retries."""
from datetime import datetime, timedelta, timezone
import os
import secrets
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import click
import requests
from flask import current_app, flash, redirect, render_template, request, url_for
from sqlalchemy import or_, update
from sqlalchemy.exc import IntegrityError

from auth import current_user, login_required
from models import db, User, Course, Assignment, StudySession, ReminderPreference, DeadlineNotification

ALLOWED_HOURS = (1, 3, 6, 7, 12, 24, 48)


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def remaining_hours(assignment):
    done = db.session.query(db.func.coalesce(db.func.sum(StudySession.duration_hours), 0)).filter_by(
        assignment_id=assignment.id, user_id=assignment.course.user_id, completed=True).scalar()
    return max(0.0, assignment.estimated_hours - done)


def deadline_utc(assignment, preference):
    if assignment.due_date is None:
        return None
    value = assignment.due_date
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo(assignment.canvas_timezone or preference.timezone))
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def offsets(preference):
    return {int(h) for h in preference.hours.split(',') if h.isdigit() and int(h) in ALLOWED_HOURS}


class DeliveryError(Exception):
    def __init__(self, code, retryable=False):
        self.code, self.retryable = code, retryable


class ResendEmail:
    """Email adapter. A future SMS adapter must provide equivalent idempotency guarantees."""
    def __init__(self, api_key):
        self.api_key = api_key

    def send(self, payload, key):
        try:
            with requests.Session() as client:
                client.trust_env = False
                result = client.post('https://api.resend.com/emails', json=payload,
                                     headers={'Authorization': 'Bearer ' + self.api_key,
                                              'Idempotency-Key': key},
                                     timeout=(5, 20), allow_redirects=False)
            if result.status_code == 200:
                data = result.json()
                if isinstance(data, dict) and isinstance(data.get('id'), str):
                    return data['id']
                raise DeliveryError('invalid_provider_response', True)
            raise DeliveryError(f'provider_http_{result.status_code}',
                                result.status_code in (408, 429) or result.status_code >= 500)
        except (requests.RequestException, ValueError):
            # Do not log exception text: it may contain headers or private content.
            raise DeliveryError('provider_connection_or_response_error', True) from None


def payload_for(assignment, user, preference, sender, now):
    due = deadline_utc(assignment, preference)
    display = due.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(preference.timezone))
    hours = remaining_hours(assignment)
    return {
        'from': sender, 'to': [user.email],
        'subject': 'StudyFlow assignment deadline reminder',
        'text': (f'Assignment: {assignment.title}\nCourse: {assignment.course.name}\n'
                 f'Deadline: {display.strftime("%Y-%m-%d %I:%M %p %Z")}\n'
                 f'Remaining estimated study hours: {hours:.2f}\n'
                 f'Information checked at: {now.isoformat()} UTC.\n\n'
                 'Manage or disable email reminders on the Reminders page in StudyFlow.'),
    }


def eligible(notification, now):
    preference = db.session.get(ReminderPreference, notification.user_id)
    assignment = db.session.get(Assignment, notification.assignment_id)
    if not preference or not preference.enabled or not assignment:
        return None
    if assignment.course.user_id != notification.user_id or notification.hours_before not in offsets(preference):
        return None
    try:
        due = deadline_utc(assignment, preference)
    except (ValueError, ZoneInfoNotFoundError):
        return None
    if due != notification.deadline_utc or due <= now or remaining_hours(assignment) <= 0.000001:
        return None
    return assignment, db.session.get(User, notification.user_id), preference


def run_reminders(now=None, sender=None, provider=None):
    """One polling pass. Safe to run from cron or the persistent worker, not an HTTP request."""
    now = now or utcnow()
    sender = sender or current_app.config.get('REMINDER_FROM')
    if provider is None:
        key = current_app.config.get('RESEND_API_KEY')
        if not key or not sender:
            current_app.logger.error('reminder_delivery_not_configured')
            raise RuntimeError('Set RESEND_API_KEY and STUDYFLOW_REMINDER_FROM before starting reminders.')
        provider = ResendEmail(key)
    if not sender:
        raise RuntimeError('An email sender is required.')
    counts = dict(queued=0, sent=0, retry=0, failed=0, cancelled=0)
    # Catch up newly due reminders for at most 15 minutes; do not burst old reminders.
    for preference in ReminderPreference.query.filter_by(enabled=True).all():
        assignments = Assignment.query.join(Course).filter(Course.user_id == preference.user_id).all()
        for assignment in assignments:
            try:
                due = deadline_utc(assignment, preference)
            except (ValueError, ZoneInfoNotFoundError):
                current_app.logger.error('reminder_invalid_timezone assignment_id=%s', assignment.id)
                continue
            if due is None or due <= now or remaining_hours(assignment) <= 0.000001:
                continue
            for hours in offsets(preference):
                scheduled = due - timedelta(hours=hours)
                if not scheduled <= now <= scheduled + timedelta(minutes=15):
                    continue
                exists = DeadlineNotification.query.filter_by(assignment_id=assignment.id, deadline_utc=due,
                                                               hours_before=hours, channel='email').first()
                if exists:
                    continue
                # Unique constraint resolves concurrent enqueuers without duplicate notifications.
                try:
                    with db.session.begin_nested():
                        db.session.add(DeadlineNotification(user_id=preference.user_id, assignment_id=assignment.id,
                            deadline_utc=due, hours_before=hours, delivery_key=secrets.token_hex(24), next_attempt_at=now))
                        db.session.flush()
                    counts['queued'] += 1
                except IntegrityError:
                    pass
    db.session.commit()
    ids = [r.id for r in DeadlineNotification.query.filter(
        DeadlineNotification.status.in_(['pending', 'retry', 'sending']),
        DeadlineNotification.next_attempt_at <= now).all()]
    for nid in ids:
        # Atomic lease prevents two workers from sending simultaneously. Timeout < lease.
        claimed = db.session.execute(update(DeadlineNotification).where(
            DeadlineNotification.id == nid,
            DeadlineNotification.status.in_(['pending', 'retry', 'sending']),
            or_(DeadlineNotification.lease_until.is_(None), DeadlineNotification.lease_until <= now)
        ).values(status='sending', lease_until=now + timedelta(minutes=2)))
        db.session.commit()
        if claimed.rowcount != 1:
            continue
        db.session.expire_all()
        item = db.session.get(DeadlineNotification, nid)
        values = eligible(item, now)
        if values is None:
            item.status = 'cancelled'; item.lease_until = None
            db.session.commit(); counts['cancelled'] += 1
            continue
        if item.first_attempt_at and now - item.first_attempt_at >= timedelta(hours=23):
            # Provider keys expire after 24h. Never retry an uncertain send after that boundary.
            item.status = 'failed'; item.last_error = 'idempotency_window_expired'; item.lease_until = None
            current_app.logger.error('reminder_failed id=%s code=%s', nid, item.last_error)
            db.session.commit(); counts['failed'] += 1
            continue
        assignment, user, preference = values
        first = item.first_attempt_at or now
        payload = payload_for(assignment, user, preference, sender, first)
        if item.payload is not None and item.payload != payload:
            # Reusing a provider key with different content is unsafe after an ambiguous send.
            item.status = 'cancelled'; item.last_error = 'content_changed_after_attempt'; item.lease_until = None
            current_app.logger.warning('reminder_cancelled id=%s code=%s', nid, item.last_error)
            db.session.commit(); counts['cancelled'] += 1
            continue
        item.payload = payload; item.first_attempt_at = first; item.attempts += 1
        db.session.commit()  # Persist the immutable payload/key before the external side effect.
        try:
            provider_id = provider.send(payload, item.delivery_key)
        except DeliveryError as error:
            item.status = 'retry' if error.retryable else 'failed'
            item.last_error = error.code
            item.next_attempt_at = now + timedelta(minutes=min(30, 2 ** min(item.attempts, 5)))
            counts[item.status] += 1
            current_app.logger.error('reminder_delivery_failed id=%s code=%s attempt=%s', nid, error.code, item.attempts)
        else:
            item.status = 'sent'; item.sent_at = now; item.provider_id = provider_id; item.last_error = None
            counts['sent'] += 1
            current_app.logger.info('reminder_accepted id=%s', nid)
        item.lease_until = None
        db.session.commit()
    return counts


def register_reminders(app):
    app.config['RESEND_API_KEY'] = os.environ.get('RESEND_API_KEY')
    app.config['REMINDER_FROM'] = os.environ.get('STUDYFLOW_REMINDER_FROM')

    @app.route('/reminders', methods=['GET', 'POST'])
    @login_required
    def reminder_settings():
        user = current_user()
        preference = db.session.get(ReminderPreference, user.id)
        error = None
        if request.method == 'POST':
            enabled = request.form.get('enabled') == 'on'
            values = request.form.getlist('hours')
            zone = request.form.get('timezone', '').strip()
            try:
                selected = {int(v) for v in values}
                if not selected.issubset(ALLOWED_HOURS) or (enabled and not selected):
                    raise ValueError()
                ZoneInfo(zone)
            except (ValueError, ZoneInfoNotFoundError):
                error = 'Choose supported reminder times and a valid timezone (for example America/Chicago).'
            else:
                if preference is None:
                    preference = ReminderPreference(user_id=user.id)
                    db.session.add(preference)
                preference.enabled = enabled
                preference.hours = ','.join(map(str, sorted(selected)))
                preference.timezone = zone
                db.session.commit()
                flash('Email reminder preferences saved.')
                return redirect(url_for('reminder_settings'))
        return render_template('reminders.html', preference=preference, error=error,
                               choices=ALLOWED_HOURS, selected=offsets(preference) if preference else {6, 7},
                               email=user.email), 400 if error else 200

    @app.cli.command('reminders')
    @click.option('--loop', is_flag=True, help='Keep running; default is a single cron-friendly pass.')
    @click.option('--interval', default=60, type=click.IntRange(30, 300), help='Seconds between worker passes.')
    def reminders_command(loop, interval):
        """Deliver due email reminders independently of browser activity."""
        while True:
            try:
                click.echo(str(run_reminders()))
            except RuntimeError as error:
                raise click.ClickException(str(error)) from None
            finally:
                db.session.remove()
            if not loop:
                return
            time.sleep(interval)
