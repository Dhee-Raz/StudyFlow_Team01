"""Test scheduling using lightweight assignments instead of database rows."""

from dataclasses import dataclass, field
from datetime import date, timedelta
from itertools import count

import pytest

from spaced_scheduler import DAILY_CAPACITY_HOURS, build_schedule, generate_study_sessions

_id_counter = count(1)


@dataclass
class FakeAssignment:
    """Minimal scheduler input; generate unique IDs so tasks stay distinct when merging."""
    due_date: date
    estimated_hours: float
    id: int = field(default_factory=lambda: next(_id_counter))


def test_past_due_date_returns_empty_list():
    """Verify that past due date returns empty list."""
    assignment = FakeAssignment(due_date=date.today() - timedelta(days=1), estimated_hours=5)
    assert generate_study_sessions(assignment) == []


def test_zero_estimated_hours_returns_empty_list():
    """Verify that zero estimated hours returns empty list."""
    assignment = FakeAssignment(due_date=date.today() + timedelta(days=7), estimated_hours=0)
    assert generate_study_sessions(assignment) == []


def test_due_tomorrow_gives_at_least_one_session():
    """Verify that due tomorrow gives at least one session."""
    assignment = FakeAssignment(due_date=date.today() + timedelta(days=1), estimated_hours=2)
    sessions = generate_study_sessions(assignment)
    assert len(sessions) >= 1


def test_short_deadline_compresses_to_a_single_session_on_the_due_date():
    # 3 days out is at/under the compressed-mode threshold - splitting it
    # across multiple daily sessions just reads as the same assignment
    # repeating with no clear due date, so it collapses to one session.
    """Verify that short deadline compresses to a single session on the due date."""
    assignment = FakeAssignment(due_date=date.today() + timedelta(days=3), estimated_hours=6)
    sessions = generate_study_sessions(assignment)

    assert len(sessions) == 1
    assert sessions[0]["date"] == date.today() + timedelta(days=3)
    assert sessions[0]["duration"] == 6


def test_longer_deadline_uses_growing_sm2_intervals():
    # 30 days out is enough runway for SM-2's growing-interval progression
    # (1, 6, then *2.5 each time) rather than one session per day.
    """Verify that longer deadline uses growing sm2 intervals."""
    assignment = FakeAssignment(due_date=date.today() + timedelta(days=30), estimated_hours=10)
    sessions = generate_study_sessions(assignment)
    dates = [s["date"] for s in sessions]

    assert len(dates) < 30  # not a session every single day
    assert dates == sorted(dates)  # strictly increasing
    assert len(dates) == len(set(dates))  # no duplicate days
    assert dates[-1] == date.today() + timedelta(days=30)  # final review lands on the due date


def test_session_hours_sum_to_estimated_hours():
    """Verify that session hours sum to estimated hours."""
    assignment = FakeAssignment(due_date=date.today() + timedelta(days=30), estimated_hours=10)
    sessions = generate_study_sessions(assignment)
    assert sum(s["duration"] for s in sessions) == pytest.approx(10, abs=0.1)


def test_first_session_gets_more_time_than_a_middle_session():
    """Verify that first session gets more time than a middle session."""
    assignment = FakeAssignment(due_date=date.today() + timedelta(days=30), estimated_hours=10)
    sessions = generate_study_sessions(assignment)
    assert len(sessions) >= 3
    assert sessions[0]["duration"] > sessions[1]["duration"]


def test_build_schedule_spreads_out_colliding_first_sessions():
    # Regression: every assignment's SM-2 progression starts with a
    # session "tomorrow", so many assignments due around the same time
    # used to all dump their first session onto the same single day.
    """Verify that build schedule spreads out colliding first sessions."""
    today = date.today()
    assignments = [
        FakeAssignment(due_date=today + timedelta(days=30), estimated_hours=2)
        for _ in range(20)
    ]
    schedule = build_schedule(assignments, today=today)

    tomorrow = today + timedelta(days=1)
    hours_tomorrow = sum(s["duration"] for s in schedule.get(tomorrow, []))
    assert hours_tomorrow <= DAILY_CAPACITY_HOURS + 0.01

    # every session's hours should have been placed somewhere, none
    # silently dropped (using hours rather than a session count since
    # same-assignment same-day sessions get merged into one entry)
    total_hours_in = sum(s["duration"] for a in assignments for s in generate_study_sessions(a, today=today))
    total_hours_out = sum(s["duration"] for sessions in schedule.values() for s in sessions)
    assert total_hours_in == pytest.approx(total_hours_out, abs=0.1)


def test_build_schedule_respects_capacity_when_due_dates_are_staggered():
    # Realistic case: due dates spread out, so there's room to push
    # colliding sessions forward without hitting any hard deadline.
    """Verify that build schedule respects capacity when due dates are staggered."""
    today = date.today()
    assignments = [
        FakeAssignment(due_date=today + timedelta(days=30 + i * 3), estimated_hours=2)
        for i in range(20)
    ]
    schedule = build_schedule(assignments, today=today)

    for day_sessions in schedule.values():
        total = sum(s["duration"] for s in day_sessions)
        assert total <= DAILY_CAPACITY_HOURS + 0.01


def test_build_schedule_allows_overflow_only_on_a_shared_hard_deadline():
    # Edge case: if 30 assignments are all due the exact same day, their
    # final reviews have nowhere left to go but that day - capacity can't
    # override a due date. Overflow should be confined to that one day.
    """Verify that build schedule allows overflow only on a shared hard deadline."""
    today = date.today()
    due = today + timedelta(days=60)
    assignments = [FakeAssignment(due_date=due, estimated_hours=2) for _ in range(30)]
    schedule = build_schedule(assignments, today=today)

    for day, day_sessions in schedule.items():
        total = sum(s["duration"] for s in day_sessions)
        if day != due:
            assert total <= DAILY_CAPACITY_HOURS + 0.01


def test_build_schedule_never_pushes_a_session_past_its_due_date():
    """Verify that build schedule never pushes a session past its due date."""
    today = date.today()
    due = today + timedelta(days=2)
    # Deliberately overload day+1 with unrelated assignments due far later,
    # so this one is forced to compete for room right up against its due date.
    crowd = [FakeAssignment(due_date=today + timedelta(days=90), estimated_hours=5) for _ in range(10)]
    tight = FakeAssignment(due_date=due, estimated_hours=1)

    schedule = build_schedule(crowd + [tight], today=today)

    tight_sessions = [
        s for sessions in schedule.values() for s in sessions if s["assignment"] is tight
    ]
    assert all(s["date"] <= due for s in tight_sessions)


def test_build_schedule_same_assignment_ordering_never_overshoots_due_date():
    # Regression: if an assignment's early sessions get bumped forward by
    # capacity pressure, the "keep this assignment's own sessions in
    # order" rule could previously cascade a later session one day at a
    # time until it landed *after* the due date. Stress it with many
    # assignments sharing one due date far enough out to use SM-2's
    # multi-session progression (day+1, day+7, day+22, due) rather than
    # the single-session compressed mode used for near-term deadlines -
    # every wave (day+1, day+7, day+22, due) is congested at once.
    """Verify that build schedule same assignment ordering never overshoots due date."""
    today = date.today()
    due = today + timedelta(days=60)
    assignments = [FakeAssignment(due_date=due, estimated_hours=3) for _ in range(100)]

    schedule = build_schedule(assignments, today=today)

    for day, sessions in schedule.items():
        for session in sessions:
            assert day <= session["assignment"].due_date


def test_build_schedule_merges_same_assignment_sessions_on_the_same_day():
    # Same stress scenario: heavy congestion at every wave of the SM-2
    # progression should be able to cascade two of one assignment's own
    # sessions onto the same day. They must appear as one merged entry
    # per assignment per day, not duplicate rows, with no hours lost.
    """Verify that build schedule merges same assignment sessions on the same day."""
    today = date.today()
    due = today + timedelta(days=60)
    assignments = [FakeAssignment(due_date=due, estimated_hours=3) for _ in range(100)]

    schedule = build_schedule(assignments, today=today)

    for day, sessions in schedule.items():
        assignment_ids = [s["assignment"].id for s in sessions]
        assert len(assignment_ids) == len(set(assignment_ids)), f"duplicate assignment on {day}"

    # sanity check that this scenario actually exercises the merge path -
    # if it stops doing so (e.g. after a future algorithm change), the
    # assertions above would otherwise pass vacuously
    ideal_session_count = sum(len(generate_study_sessions(a, today=today)) for a in assignments)
    actual_session_count = sum(len(sessions) for sessions in schedule.values())
    assert actual_session_count < ideal_session_count, "scenario no longer triggers any merges"

    for assignment in assignments:
        unbalanced_total = sum(s["duration"] for s in generate_study_sessions(assignment, today=today))
        scheduled_total = sum(
            s["duration"] for sessions in schedule.values() for s in sessions if s["assignment"] is assignment
        )
        assert scheduled_total == pytest.approx(unbalanced_total, abs=0.01)


def test_build_schedule_keeps_same_day_when_under_capacity():
    """Verify that build schedule keeps same day when under capacity."""
    today = date.today()
    assignment = FakeAssignment(due_date=today + timedelta(days=30), estimated_hours=2)
    schedule = build_schedule([assignment], today=today)

    unbalanced = {s["date"]: s for s in generate_study_sessions(assignment, today=today)}
    for day, sessions in schedule.items():
        assert day in unbalanced
