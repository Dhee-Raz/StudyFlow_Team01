from datetime import date, datetime, timedelta
from types import SimpleNamespace

from presentation import course_groups, schedule_sections


def item(i, day, **values):
    return SimpleNamespace(id=i, title='Task', due_date=datetime.combine(day, datetime.min.time()) if day else None,
                           estimated_hours=values.get('hours', 2), canvas_key='canvas',
                           points_possible=values.get('points', 10), is_exam=values.get('exam', False))


def test_groups_collapse_later_and_past_and_keep_missing():
    today = date(2026, 10, 19)
    items = [item(i, today + timedelta(days=i)) for i in range(6)]
    items += [item(10, today - timedelta(days=1)), item(11, None)]
    result = course_groups([SimpleNamespace(assignments=items)], today)[0]
    assert result['upcoming_count'] == 6
    assert len(result['visible_groups']) == 4 and len(result['extra_groups']) == 2
    assert result['visible_groups'][0]['due_soon']
    assert result['visible_groups'][1]['due_soon']
    assert not result['visible_groups'][2]['due_soon']
    assert len(result['past']) == len(result['undated']) == 1


def test_exams_today_priority_and_long_schedule():
    today = date(2026, 10, 19)
    exam = item(1, today + timedelta(days=3), exam=True)
    low = item(2, today, points=2)
    high = item(3, today, points=100)
    future = item(4, today + timedelta(days=70))
    sections = schedule_sections([exam, low, high, future, item(5, None)], today)
    assert sections['due_today'] == [high, low]
    assert sections['exams'] == [dict(assignment=exam, days_until=3)]
    assert sections['extra_days']
    assert all(s['assignment'] is future for _, sessions in sections['visible_days'] + sections['extra_days'] for s in sessions)
