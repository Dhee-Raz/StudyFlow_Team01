"""Ticket 10: initial Canvas import. Existing imported records are never refreshed."""
from datetime import datetime
import math
from urllib.parse import urlsplit, urljoin
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from sqlalchemy.exc import SQLAlchemyError

from models import db, Course, Assignment


class CanvasImportError(ValueError):
    pass


def pages(http, base_url, path):
    """Follow Canvas pagination only on the authenticated origin and API path."""
    url = base_url + path
    expected = urlsplit(url)
    visited = set()
    for _ in range(100):
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or parsed.netloc != expected.netloc
                or parsed.path != expected.path or parsed.username or parsed.password
                or parsed.fragment or url in visited):
            raise CanvasImportError('Canvas returned an unsafe or repeated pagination link. Nothing was imported.')
        visited.add(url)
        response = http.get(url, timeout=(5, 20), allow_redirects=False)
        if response.status_code in (401, 403):
            raise CanvasImportError('Canvas denied access. Reconnect with a valid token that can read courses and assignments.')
        if response.status_code == 429:
            raise CanvasImportError('Canvas is busy. Wait a moment before importing again.')
        if response.status_code != 200:
            raise CanvasImportError('Canvas could not complete the import. Your planner has not changed.')
        data = response.json()
        if not isinstance(data, list):
            raise CanvasImportError('Canvas returned an unexpected list. Your planner has not changed.')
        yield from data
        next_url = response.links.get('next', {}).get('url')
        if not next_url:
            return
        url = urljoin(url, next_url)
    raise CanvasImportError('The import exceeded the pagination limit. Your planner has not changed.')


def numeric_id(value):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError('Invalid Canvas ID')
    return value


def deadline(value, zone):
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Canvas deadline must include its timezone')
    return parsed.astimezone(ZoneInfo(zone)).replace(tzinfo=None)


def import_courses(record):
    base_url = record['url']
    account = numeric_id(record['profile']['id'])
    prefix = f'{base_url}|{account}|'
    zone = record['profile'].get('time_zone') or 'UTC'
    try:
        ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError):
        zone = 'UTC'
    counts = dict(courses=0, assignments=0, skipped=0, failed=0, missing_deadlines=0)
    courses = {}
    # Fetch everything before writing: a failed page must not leave a partial import.
    try:
        with requests.Session() as http:
            http.trust_env = False
            http.headers.update({'Authorization': 'Bearer ' + record['token'], 'Accept': 'application/json'})
            for raw in pages(http, base_url, '/api/v1/courses?enrollment_type=student&enrollment_state=active&per_page=100'):
                try:
                    cid = numeric_id(raw['id'])
                    name = raw['name'].strip()
                    if not name:
                        raise ValueError()
                    if raw.get('workflow_state') not in (None, 'available'):
                        counts['skipped'] += 1
                        continue
                except (KeyError, TypeError, ValueError, AttributeError):
                    counts['failed'] += 1
                    continue
                if cid in courses:
                    counts['skipped'] += 1
                    continue
                items = {}
                for item in pages(http, base_url, f'/api/v1/courses/{cid}/assignments?per_page=100'):
                    try:
                        aid = numeric_id(item['id'])
                        title = item['name'].strip()
                        if not title:
                            raise ValueError()
                        due = deadline(item.get('due_at'), zone)
                        points = item.get('points_possible')
                        if points is not None:
                            points = float(points)
                            if not math.isfinite(points) or points < 0:
                                raise ValueError()
                    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
                        counts['failed'] += 1
                        continue
                    if aid in items:
                        counts['skipped'] += 1
                        continue
                    items[aid] = dict(title=title[:200], due_date=due, points_possible=points,
                                      canvas_url=f'{base_url}/courses/{cid}/assignments/{aid}',
                                      canvas_timezone=zone)
                courses[cid] = (name[:100], items)
    except (requests.RequestException, ValueError) as error:
        if isinstance(error, CanvasImportError):
            raise
        raise CanvasImportError('Could not read Canvas securely. Your planner has not changed; try again.') from None

    try:
        for cid, (name, items) in courses.items():
            course_key = prefix + str(cid)
            course = Course.query.filter_by(canvas_key=course_key).first()
            if course is None:
                course = Course(name=name, canvas_key=course_key)
                db.session.add(course)
                counts['courses'] += 1
            else:
                counts['skipped'] += 1
            for aid, values in items.items():
                key = course_key + '|' + str(aid)
                if Assignment.query.filter_by(canvas_key=key).first() is not None:
                    counts['skipped'] += 1
                    continue
                db.session.add(Assignment(course=course, canvas_key=key, estimated_hours=0, **values))
                counts['assignments'] += 1
                counts['missing_deadlines'] += values['due_date'] is None
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        raise CanvasImportError('Could not save the import. Your planner has not changed; try again.') from None
    return counts
