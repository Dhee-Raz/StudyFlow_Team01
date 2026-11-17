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
    schedule_history_days = db.Column(db.String(3), nullable=False, default="7", server_default="7")
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
    # Deleting a course also deletes its notes and PDF records.
    # (PDF files on disk are removed by library.remove_stored_files.)
    notes = db.relationship(
        "CourseNote", backref="course", lazy=True,
        cascade="all, delete-orphan",
    )
    materials = db.relationship(
        "CourseMaterial", backref="course", lazy=True,
        cascade="all, delete-orphan",
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

    # One plain-text memo per assignment (deleted with the assignment).
    memo = db.relationship(
        "AssignmentMemo",
        backref="assignment",
        uselist=False,
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


class CourseNote(db.Model):
    """A plain-text note kept in a course's materials library."""

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    course_id = db.Column(db.Integer, db.ForeignKey("course.id"), nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    body = db.Column(db.Text, nullable=False, default="")
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class CourseMaterial(db.Model):
    """Metadata for an uploaded PDF. The file itself lives on disk, outside static/."""

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    course_id = db.Column(db.Integer, db.ForeignKey("course.id"), nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    original_filename = db.Column(db.String(255), nullable=False)
    # Random server-generated name (<32 hex chars>.pdf); never derived from user input.
    stored_filename = db.Column(db.String(64), nullable=False, unique=True)
    size_bytes = db.Column(db.Integer, nullable=False)
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )


class AssignmentMemo(db.Model):
    """A short plain-text memo attached to one assignment."""

    id = db.Column(db.Integer, primary_key=True)
    assignment_id = db.Column(
        db.Integer, db.ForeignKey("assignment.id"), nullable=False, unique=True
    )
    body = db.Column(db.Text, nullable=False, default="")
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
