from datetime import date, datetime, timedelta
from types import SimpleNamespace
import unittest

from schedule_history import history_view


class ScheduleHistoryTests(unittest.TestCase):
    def setUp(self):
        self.today = date(2026, 11, 17)
        self.assignments = [
            SimpleNamespace(id=i, due_date=datetime.combine(self.today + timedelta(days=offset), datetime.min.time()), estimated_hours=3)
            for i, offset in enumerate([-40, -15, -7, -1, 0, 5])
        ]

    def test_ranges_and_inclusive_boundary(self):
        for choice, expected in [("0", {4, 5}), ("7", {2, 3, 4, 5}), ("14", {2, 3, 4, 5}), ("30", {1, 2, 3, 4, 5}), ("all", set(range(6)))]:
            with self.subTest(choice=choice):
                view = history_view(self.assignments, [], self.today, choice)
                self.assertEqual(view["visible_assignment_ids"], expected)

    def test_overdue_remaining_and_completed(self):
        sessions = [SimpleNamespace(assignment_id=0, completed=True, duration_hours=3),
                    SimpleNamespace(assignment_id=1, completed=True, duration_hours=1)]
        view = history_view(self.assignments, sessions, self.today, "7")
        self.assertEqual([(x["assignment"].id, x["remaining"]) for x in view["older_overdue"]], [(1, 2)])
        self.assertEqual(len(self.assignments), 6)
        self.assertEqual(len(sessions), 2)

    def test_invalid_choice_defaults_to_seven_days(self):
        self.assertEqual(history_view(self.assignments, [], self.today, "invalid")["history_choice"], "7")
