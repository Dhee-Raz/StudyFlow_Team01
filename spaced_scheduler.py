"""Calculate study sessions in memory; this module never writes to the database.
Uses fixed growing review intervals, weighted hours, and a soft daily capacity limit."""

from datetime import date, timedelta

# SM-2's default progression: first review after 1 day, then 6 days, then
# each subsequent interval multiplied by the ease factor (2.5 is SM-2's
# default for a "perfect recall" review - we have no recall-quality input
# yet, so we always use the default rather than adjusting it per review).
SM2_INITIAL_INTERVAL = 1
SM2_SECOND_INTERVAL = 6
SM2_EASE_FACTOR = 2.5

# Below this many days of runway there isn't room for spaced-repetition to
# add value - splitting a 3-day-out assignment across multiple daily
# sessions just reads as the same item repeating on several days with no
# real due date at a glance. So it collapses to one session on the due
# date instead.
COMPRESSED_THRESHOLD_DAYS = 3

# Placeholder daily study budget - every assignment's SM-2 progression
# starts with a session "tomorrow", so with many active assignments their
# first sessions all land on the same day unless something spreads the
# load out. No per-user capacity setting exists yet (the original pitch's
# "tell it how much time you have per day" was never wired up), so this is
# a fixed default until that's built.
DAILY_CAPACITY_HOURS = 3.0


def _review_offsets(days_available):
    """Day offsets (from today) for review sessions, using SM-2's growing
    interval progression, always ending with a final session on the due
    date itself."""
    if days_available <= COMPRESSED_THRESHOLD_DAYS:
        return [days_available]

    offsets = [SM2_INITIAL_INTERVAL]
    interval = SM2_SECOND_INTERVAL
    while offsets[-1] + interval < days_available:
        offsets.append(offsets[-1] + interval)
        interval = round(interval * SM2_EASE_FACTOR)

    if offsets[-1] != days_available:
        offsets.append(days_available)

    return offsets


def _session_weights(session_count):
    """Front-load the first session (initial learning) and the last
    (final review before it's due); middle sessions are lighter spaced
    touch-ups, per spaced-repetition practice."""
    if session_count == 1:
        return [1.0]

    weights = [1.0] * session_count
    weights[0] = 2.0
    weights[-1] = max(weights[-1], 1.5)
    return weights


def generate_study_sessions(assignment, today=None):
    """Generate spaced-repetition review sessions for an assignment,
    following SM-2's growing-interval progression rather than spreading
    hours evenly across every day until it's due.

    Returns a list of dicts: {"date": date, "duration": float, "assignment": assignment}.
    """
    today = today or date.today()
    due_date = assignment.due_date
    estimated_hours = assignment.estimated_hours

    if not due_date or not estimated_hours or estimated_hours <= 0:
        return []

    days_available = (due_date - today).days
    if days_available <= 0:
        return []

    # Offsets choose dates; relative weights split the total estimated effort.
    offsets = _review_offsets(days_available)
    weights = _session_weights(len(offsets))
    weight_total = sum(weights)

    # Round each duration independently; totals can differ slightly due to rounding.
    return [
        {
            "date": today + timedelta(days=offset),
            "duration": round(estimated_hours * weight / weight_total, 2),
            "assignment": assignment,
        }
        for offset, weight in zip(offsets, weights)
    ]


def build_schedule(assignments, today=None, daily_capacity_hours=DAILY_CAPACITY_HOURS):
    """Generate every assignment's ideal SM-2 sessions, then rebalance so no
    single day is overloaded: whenever adding a session would push a day
    past the daily capacity, it's bumped to the next day with room -
    never past that assignment's own due date, since the session has to
    happen before then regardless of how full the calendar is.

    Sessions are placed in order of when they were originally wanted, and
    same-day ties go to whichever assignment is due soonest (it has the
    least slack to absorb a delay). Each assignment's own sessions are
    also kept in non-decreasing date order, so a bumped early session
    can't land after that same assignment's later session.

    Returns a dict: {date: [session, ...]}.
    """
    today = today or date.today()

    all_sessions = []
    for assignment in assignments:
        all_sessions.extend(generate_study_sessions(assignment, today=today))

    all_sessions.sort(key=lambda s: (s["date"], s["assignment"].due_date))

    # Track hours per day and the last placement per persistent assignment ID.
    day_load = {}
    last_placed_date = {}
    schedule = {}

    for session in all_sessions:
        assignment = session["assignment"]
        placed_date = session["date"]

        if assignment.id in last_placed_date:
            placed_date = max(placed_date, last_placed_date[assignment.id] + timedelta(days=1))
            # The monotonic push above can overshoot the due date if an
            # earlier session for this same assignment already got bumped
            # all the way there - clamp back rather than violate it.
            placed_date = min(placed_date, assignment.due_date)

        # Capacity is a soft limit: the deadline wins if no later day has room.
        while (
            day_load.get(placed_date, 0.0) + session["duration"] > daily_capacity_hours
            and placed_date < assignment.due_date
        ):
            placed_date += timedelta(days=1)

        session["date"] = placed_date
        day_load[placed_date] = day_load.get(placed_date, 0.0) + session["duration"]
        last_placed_date[assignment.id] = placed_date
        # Create the day bucket if needed, then append this session to it.
        schedule.setdefault(placed_date, []).append(session)

    return _merge_same_assignment_same_day(schedule)


def _merge_same_assignment_same_day(schedule):
    """Capacity/due-date clamping can land two of the same assignment's
    sessions on the same day - combine those into a single entry with
    the summed duration instead of showing the assignment twice."""
    merged = {}
    for day, sessions in schedule.items():
        by_assignment = {}
        order = []
        for session in sessions:
            assignment_id = session["assignment"].id
            if assignment_id in by_assignment:
                by_assignment[assignment_id]["duration"] = round(
                    by_assignment[assignment_id]["duration"] + session["duration"], 2
                )
            else:
                by_assignment[assignment_id] = dict(session)
                order.append(assignment_id)
        merged[day] = [by_assignment[assignment_id] for assignment_id in order]
    return merged
