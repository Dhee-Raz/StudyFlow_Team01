from datetime import datetime, timedelta, timezone


def generate_study_sessions(assignment):
    """
    Generate a basic study schedule for an assignment.

    Each session contains:
    - date
    - duration in hours
    - assignment reference
    """

    # Handle invalid or zero estimated study time.
    if assignment.estimated_hours <= 0:
        return []

    due_date = assignment.due_date

    # Support both timezone-aware and timezone-naive datetimes.
    if due_date.tzinfo is not None:
        today = datetime.now(timezone.utc).date()
    else:
        today = datetime.now().date()

    due_day = due_date.date()

    days_available = (due_day - today).days

    # A past due date cannot have future study sessions.
    if days_available < 0:
        return []

    # If the assignment is due today, still create one session.
    if days_available == 0:
        days_available = 1

    hours_per_day = assignment.estimated_hours / days_available

    sessions = []

    for day in range(days_available):
        session_date = today + timedelta(days=day)

        sessions.append(
            {
                "date": session_date,
                "duration": hours_per_day,
                "assignment": assignment,
            }
        )

    return sessions
