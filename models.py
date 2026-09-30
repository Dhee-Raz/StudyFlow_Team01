from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class Course(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    created_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )
    assignments = db.relationship(
        "Assignment",
        backref="course",
        lazy=True
    )


class Assignment(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    # Optional so personal tasks (not tied to a course) can be saved.
    course_id = db.Column(
        db.Integer,
        db.ForeignKey("course.id"),
        nullable=True
    )

    title = db.Column(db.String(200), nullable=False)
    due_date = db.Column(db.DateTime, nullable=False)
    estimated_hours = db.Column(db.Float, nullable=False)

    # "Academic" or "Personal" — shown on the dashboard.
    category = db.Column(db.String(20), nullable=False, default="Academic")

    created_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )