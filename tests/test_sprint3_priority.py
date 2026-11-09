"""Sprint 3 Ticket 1: scheduling priorities and capacity regression tests."""
import unittest
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from scheduler import plan_sessions


class PrioritySchedulingTests(unittest.TestCase):
    def assignment(self, identifier, hours, due):
        return SimpleNamespace(id=identifier, estimated_hours=hours, due_date=datetime.combine(due, datetime.min.time()))

    def test_earlier_deadline_takes_priority(self):
        today = date(2026, 11, 9)
        earlier = self.assignment(1, 3, today + timedelta(days=1))
        later = self.assignment(2, 3, today + timedelta(days=4))
        plan, summaries = plan_sessions([later, earlier], today, daily_capacity=3)
        self.assertEqual(plan[(1, today)], 3)
        self.assertEqual(summaries[1]["unscheduled"], 0)

    def test_larger_workload_wins_equal_deadline(self):
        today = date(2026, 11, 9)
        small = self.assignment(1, 1, today + timedelta(days=1))
        large = self.assignment(2, 4, today + timedelta(days=1))
        plan, summaries = plan_sessions([small, large], today, daily_capacity=3)
        self.assertEqual(plan[(2, today)], 3)
        self.assertEqual(summaries[2]["unscheduled"], 1)
        self.assertEqual(summaries[1]["unscheduled"], 1)

    def test_completed_hours_reduce_remaining_work(self):
        today = date(2026, 11, 9)
        task = self.assignment(1, 5, today + timedelta(days=2))
        plan, summaries = plan_sessions([task], today, daily_capacity=3,
            completed_hours_by_assignment={1: 2})
        self.assertAlmostEqual(sum(plan.values()), 3)
        self.assertEqual(summaries[1]["remaining"], 3)

    def test_weekday_availability_and_capacity(self):
        today = date(2026, 11, 9)  # Monday
        task = self.assignment(1, 8, today + timedelta(days=3))
        plan, summaries = plan_sessions([task], today, daily_capacity=2,
            available_weekdays={0, 2})
        self.assertTrue(all(day.weekday() in {0, 2} for _, day in plan))
        self.assertTrue(all(hours <= 2 for hours in plan.values()))
        self.assertEqual(summaries[1]["unscheduled"], 4)


if __name__ == "__main__":
    unittest.main()
