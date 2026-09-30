"""Adds a few sample tasks so the dashboard has something to show.

Run once with:  python seed_tasks.py
"""

from datetime import datetime

from app import app
from models import db, Course, Assignment

with app.app_context():
    course = Course(name="CS 3398")
    db.session.add(course)
    db.session.flush()

    db.session.add_all([
        Assignment(
            title="Sprint 1 demo",
            due_date=datetime(2026, 10, 9),
            estimated_hours=3,
            category="Academic",
            course_id=course.id,
        ),
        Assignment(
            title="Pay rent",
            due_date=datetime(2026, 10, 1),
            estimated_hours=0.5,
            category="Personal",
        ),
    ])
    db.session.commit()
    print("Added 2 sample tasks.")