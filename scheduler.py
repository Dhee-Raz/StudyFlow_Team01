from datetime import date, datetime

def generate_study_sessions(assignment, today=None):
    """Return a list of study sessions for an assignment."""
    if today is None:
        today = date.today()

    due_date = assignment.due_date
    hours = assignment.estimated_hours or 0

    # Handle missing/bad data gracefully
    if due_date is None or hours <= 0:
        return []
    if isinstance(due_date, datetime):
        due_date = due_date.date()

    days_available = (due_date - today).days
    if days_available <= 0:
        return []  # due today or already past

    # Split the work into half-hour chunks
    total_chunks = max(1, round(hours * 2))
    num_sessions = min(days_available, total_chunks)

    # Share chunks as evenly as possible across sessions
    base, extra = divmod(total_chunks, num_sessions)

    sessions = []
    for i in range(num_sessions):
        day_offset = i * days_available // num_sessions  # spreads dates out
        chunks = base + (1 if i < extra else 0)
        sessions.append({
            "date": today + timedelta(days=day_offset),
            "duration": chunks * 0.5,
            "assignment": assignment,
        })
    return sessions
