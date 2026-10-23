"""Grouped course UI and schedule sections without replacing manual scheduling."""
from datetime import date, timedelta
from itertools import groupby
from types import SimpleNamespace
from scheduler import generate_study_sessions
from spaced_scheduler import build_schedule


def course_groups(courses, today):
    result = []
    for course in courses:
        dated = sorted((a for a in course.assignments if a.due_date), key=lambda a: a.due_date)
        upcoming = [a for a in dated if a.due_date.date() >= today]
        groups = [dict(due_date=day, assignments=list(items), due_soon=day <= today + timedelta(days=1))
                  for day, items in groupby(upcoming, key=lambda a: a.due_date.date())]
        result.append(dict(course=course, upcoming_count=len(upcoming), visible_groups=groups[:4],
                           extra_groups=groups[4:], past=[a for a in dated if a.due_date.date() < today],
                           undated=[a for a in course.assignments if a.due_date is None]))
    return result


def schedule_sections(assignments, today, saved_sessions=None):
    exams = sorted((a for a in assignments if a.is_exam and a.due_date and a.due_date.date() >= today), key=lambda a: a.due_date)
    normal = [a for a in assignments if not a.is_exam]
    due_today = sorted((a for a in normal if a.due_date and a.due_date.date() == today), key=lambda a: -(a.points_possible or 0))
    if saved_sessions is not None:
        # Ticket 4: render the persisted, user-specific availability plan.
        grouped = {}
        for item in saved_sessions:
            if item.session_date >= today:
                grouped.setdefault(item.session_date, []).append({
                    "date": item.session_date,
                    "duration": item.duration_hours,
                    "assignment": item.assignment,
                    "completed": item.completed,
                })
        days = [(day, sorted(grouped[day], key=lambda row: row["assignment"].due_date or today))
                for day in sorted(grouped)]
        cutoff = today + timedelta(days=30)
        return dict(visible_days=[entry for entry in days if entry[0] <= cutoff],
                    extra_days=[entry for entry in days if entry[0] > cutoff],
                    due_today=due_today,
                    exams=[dict(assignment=a, days_until=(a.due_date.date() - today).days)
                           for a in exams])

    imported = [a for a in normal if a.canvas_key and a.due_date]
    by_id = {a.id: a for a in imported}
    proxies = [SimpleNamespace(id=a.id, due_date=a.due_date.date(), estimated_hours=a.estimated_hours) for a in imported]
    schedule = build_schedule(proxies, today=today)
    for sessions in schedule.values():
        for session in sessions:
            session['assignment'] = by_id[session['assignment'].id]
    for assignment in normal:
        if not assignment.canvas_key:
            for session in generate_study_sessions(assignment):
                schedule.setdefault(session['date'], []).append(session)
    days = [(day, sorted(schedule[day], key=lambda s: -(s['assignment'].points_possible or 0))) for day in sorted(schedule)]
    cutoff = today + timedelta(days=30)
    return dict(visible_days=[entry for entry in days if entry[0] <= cutoff],
                extra_days=[entry for entry in days if entry[0] > cutoff], due_today=due_today,
                exams=[dict(assignment=a, days_until=(a.due_date.date() - today).days) for a in exams])
