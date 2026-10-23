from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def generate_study_sessions(assignment):
    """
    Generate a basic study schedule for an assignment.

    Each session contains:
    - date
    - duration in hours
    - assignment reference
    """

    # Handle invalid or zero estimated study time.
    if assignment.estimated_hours <= 0 or assignment.due_date is None:
        return []

    due_date = assignment.due_date

    # Support both timezone-aware and timezone-naive datetimes.
    if getattr(assignment, "canvas_timezone", None):
        today = datetime.now(ZoneInfo(assignment.canvas_timezone)).date()
    elif due_date.tzinfo is not None:
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


# ---------------------------------------------------------------------------
# SCRUM-26: saved sessions that respect availability
# ---------------------------------------------------------------------------

DEFAULT_DAILY_CAPACITY_HOURS = 4.0
_EPSILON = 0.01  # ignore leftover fractions smaller than ~36 seconds


def _due_day(assignment):
    return assignment.due_date.date()


def _study_days(today, due_day):
    """Days you can study: today up to the day before the due date.

    If the assignment is due today, today is the only study day.
    """
    if due_day <= today:
        return [today]
    return [today + timedelta(days=offset) for offset in range((due_day - today).days)]


def plan_sessions(assignments, today, daily_capacity=DEFAULT_DAILY_CAPACITY_HOURS,
                  completed_hours_by_assignment=None, completed_hours_by_day=None,
                  available_weekdays=None):
    """Plan study hours across days without going over the daily capacity.

    Pure function (no database), so it is easy to test.

    - Assignments with earlier due dates get first pick of each day's hours.
    - Hours already completed count against that assignment and that day.
    - Hours that cannot fit before the due date are reported as unscheduled.

    Returns (plan, summaries):
      plan       -> {(assignment_id, day): hours}
      summaries  -> {assignment_id: {"remaining", "scheduled", "unscheduled", "status"}}
    """
    if available_weekdays is None:
        available_weekdays = set(range(7))
    else:
        available_weekdays = set(available_weekdays)
    if not available_weekdays or not available_weekdays.issubset(set(range(7))):
        raise ValueError("Select at least one valid weekday.")
    if not (0 < daily_capacity <= 24):
        raise ValueError("Daily study hours must be between 0 and 24.")
    completed_hours_by_assignment = completed_hours_by_assignment or {}
    completed_hours_by_day = completed_hours_by_day or {}

    # How many hours are still free on each day.
    free_hours = {}

    def free(day):
        if day not in free_hours:
            free_hours[day] = max(0.0, daily_capacity - completed_hours_by_day.get(day, 0.0))
        return free_hours[day]

    plan = {}
    summaries = {}

    ordered = sorted(assignments, key=lambda a: (a.due_date, a.id))
    for assignment in ordered:
        done = completed_hours_by_assignment.get(assignment.id, 0.0)
        remaining = max(0.0, (assignment.estimated_hours or 0.0) - done)
        summary = {"remaining": round(remaining, 2), "scheduled": 0.0, "unscheduled": 0.0}
        summaries[assignment.id] = summary

        if remaining < _EPSILON:
            summary["status"] = "done"
            continue
        if _due_day(assignment) < today:
            summary["status"] = "overdue"
            summary["unscheduled"] = round(remaining, 2)
            continue
        summary["status"] = "active"

        # Spread hours evenly over the open days ("water filling"):
        # give each day an equal share, and if a day is full, the
        # leftover is shared by the other days.
        open_days = [day for day in _study_days(today, _due_day(assignment)) if day.weekday() in available_weekdays and free(day) > _EPSILON]
        to_place = remaining
        while to_place > _EPSILON and open_days:
            share = to_place / len(open_days)
            still_open = []
            for day in open_days:
                hours = min(share, free(day))
                if hours > 0:
                    key = (assignment.id, day)
                    plan[key] = plan.get(key, 0.0) + hours
                    free_hours[day] -= hours
                    to_place -= hours
                if free(day) > _EPSILON:
                    still_open.append(day)
            open_days = still_open

        summary["scheduled"] = round(remaining - to_place, 2)
        summary["unscheduled"] = round(max(0.0, to_place), 2)

    plan = {key: round(hours, 2) for key, hours in plan.items() if hours >= _EPSILON}
    return plan, summaries


def refresh_saved_sessions(db, user_id, today, daily_capacity=DEFAULT_DAILY_CAPACITY_HOURS,
                           available_weekdays=None):
    """Rebuild a user's saved StudySession rows from their assignments.

    Safe to run as often as you like:
    - Completed sessions are never changed or deleted.
    - An existing upcoming session for the same assignment and day is
      updated in place instead of creating a duplicate.
    - Upcoming sessions that are no longer needed are removed.

    Returns the per-assignment summaries from plan_sessions().
    """
    from models import Assignment, Course, StudySession

    assignments = Assignment.query.join(Course).filter(Course.user_id == user_id).all()
    sessions = StudySession.query.filter_by(user_id=user_id).all()

    completed_by_assignment = {}
    completed_by_day = {}
    for session in sessions:
        if session.completed:
            completed_by_assignment[session.assignment_id] = (
                completed_by_assignment.get(session.assignment_id, 0.0) + session.duration_hours
            )
            completed_by_day[session.session_date] = (
                completed_by_day.get(session.session_date, 0.0) + session.duration_hours
            )

    plan, summaries = plan_sessions(
        assignments, today, daily_capacity, completed_by_assignment, completed_by_day,
        available_weekdays=available_weekdays
    )

    existing = {}
    for session in sessions:
        if session.completed:
            continue
        key = (session.assignment_id, session.session_date)
        if key in plan and key not in existing:
            existing[key] = session
        else:
            # Old, duplicate, or no longer needed -> remove it.
            db.session.delete(session)

    for (assignment_id, day), hours in plan.items():
        session = existing.get((assignment_id, day))
        if session is None:
            db.session.add(StudySession(
                user_id=user_id,
                assignment_id=assignment_id,
                session_date=day,
                duration_hours=hours,
            ))
        elif session.duration_hours != hours:
            session.duration_hours = hours

    db.session.commit()
    return summaries
