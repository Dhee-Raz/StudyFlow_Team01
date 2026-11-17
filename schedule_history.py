"""Presentation-only schedule history filtering."""
from datetime import timedelta

HISTORY_OPTIONS = {
    "0": "Today onward",
    "7": "Past 7 days",
    "14": "Past 14 days",
    "30": "Past 30 days",
    "all": "Entire semester",
}


def history_view(assignments, sessions, today, choice):
    """Keep future work visible and identify older work by remaining study hours."""
    if choice not in HISTORY_OPTIONS:
        choice = "7"
    start = None if choice == "all" else today - timedelta(days=int(choice))
    completed = {}
    for session in sessions:
        if session.completed:
            completed[session.assignment_id] = completed.get(session.assignment_id, 0) + session.duration_hours
    visible = set()
    older = []
    for assignment in assignments:
        due = assignment.due_date.date() if assignment.due_date else None
        if start is None or due is None or due >= start:
            visible.add(assignment.id)
        else:
            remaining = max(0, (assignment.estimated_hours or 0) - completed.get(assignment.id, 0))
            if remaining > 1e-6:
                older.append(dict(assignment=assignment, remaining=remaining))
    older.sort(key=lambda item: item["assignment"].due_date, reverse=True)
    return dict(history_start=start, history_choice=choice, history_options=HISTORY_OPTIONS,
                visible_assignment_ids=visible, older_overdue=older)
