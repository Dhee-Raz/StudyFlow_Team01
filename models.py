import re
from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import validates

db = SQLAlchemy()


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), nullable=False, unique=True)
    password_hash = db.Column(db.String(255), nullable=False)
    # ISO weekdays: Monday=0 through Sunday=6. Database defaults protect existing users.
    available_weekdays = db.Column(db.String(13), nullable=False, default="0,1,2,3,4,5,6", server_default="0,1,2,3,4,5,6")
    daily_study_hours = db.Column(db.Float, nullable=False, default=4.0, server_default="4.0")
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )
    courses = db.relationship("Course", backref="owner", lazy=True)
    personal_tasks = db.relationship("PersonalTask", backref="owner", lazy=True)

    @validates("email")
    def normalize_email(self, key, value):
        if not isinstance(value, str):
            raise ValueError("Email must be a string.")
        normalized = value.strip().lower()
        if not normalized or len(normalized) > 255 or "@" not in normalized:
            raise ValueError("Provide a valid email address.")
        return normalized


class Course(db.Model):
    canvas_key = db.Column(db.String(600), nullable=True)
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
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
    @property
    def is_exam(self):
        return bool(re.search(r"exam|mid[\s_-]?term", self.title, re.IGNORECASE))

    canvas_key = db.Column(db.String(650), nullable=True)
    canvas_missing = db.Column(db.Boolean, nullable=False, default=False, server_default="0")
    canvas_url = db.Column(db.String(600), nullable=True)
    canvas_timezone = db.Column(db.String(100), nullable=True)
    points_possible = db.Column(db.Float, nullable=True)
    id = db.Column(db.Integer, primary_key=True)

    course_id = db.Column(
        db.Integer,
        db.ForeignKey("course.id"),
        nullable=False
    )

    title = db.Column(db.String(200), nullable=False)
    due_date = db.Column(db.DateTime, nullable=True)
    estimated_hours = db.Column(db.Float, nullable=False)

    # Deleting an assignment also deletes its saved study sessions.
    study_sessions = db.relationship(
        "StudySession",
        backref="assignment",
        lazy=True,
        cascade="all, delete-orphan",
    )

    created_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )


class PersonalTask(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    due_date = db.Column(db.DateTime, nullable=False)
    details = db.Column(db.Text, nullable=False, default="")
    created_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )


class StudySession(db.Model):
    """One saved block of study time for an assignment on a specific day."""

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    assignment_id = db.Column(
        db.Integer, db.ForeignKey("assignment.id"), nullable=False, index=True
    )
    session_date = db.Column(db.Date, nullable=False, index=True)
    duration_hours = db.Column(db.Float, nullable=False)
    completed = db.Column(db.Boolean, nullable=False, default=False)
    completed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )


class ReminderPreference(db.Model):
    """Opt-in notification settings; no row means reminders are disabled."""
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), primary_key=True)
    enabled = db.Column(db.Boolean, nullable=False, default=False)
    hours = db.Column(db.String(60), nullable=False, default='6,7')
    timezone = db.Column(db.String(100), nullable=False, default='America/Chicago')


class DeadlineNotification(db.Model):
    """Durable outbox and delivery ledger; times are stored as naive UTC."""
    __table_args__ = (db.UniqueConstraint('assignment_id', 'deadline_utc', 'hours_before', 'channel', name='uq_deadline_notification'),)
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False)
    assignment_id = db.Column(db.Integer, db.ForeignKey('assignment.id', ondelete='CASCADE'), nullable=False)
    deadline_utc = db.Column(db.DateTime, nullable=False)
    hours_before = db.Column(db.Integer, nullable=False)
    channel = db.Column(db.String(20), nullable=False, default='email')
    status = db.Column(db.String(20), nullable=False, default='pending', index=True)
    delivery_key = db.Column(db.String(80), nullable=False, unique=True)
    payload = db.Column(db.JSON, nullable=True)
    first_attempt_at = db.Column(db.DateTime, nullable=True)
    next_attempt_at = db.Column(db.DateTime, nullable=False)
    lease_until = db.Column(db.DateTime, nullable=True)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    provider_id = db.Column(db.String(255), nullable=True)
    last_error = db.Column(db.String(100), nullable=True)
    sent_at = db.Column(db.DateTime, nullable=True)
