"""Ticket 1: preserve legacy data while assigning it to an explicit account."""
import sqlite3

import pytest
from werkzeug.security import check_password_hash

from scripts.migrate_users import migrate, normalize_email


@pytest.fixture
def legacy_db(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as con:
        con.executescript("""
            PRAGMA foreign_keys=ON;
            CREATE TABLE course (
                id INTEGER PRIMARY KEY, name VARCHAR(100) NOT NULL,
                created_at DATETIME NOT NULL
            );
            CREATE TABLE assignment (
                id INTEGER PRIMARY KEY, course_id INTEGER NOT NULL
                    REFERENCES course(id), title VARCHAR(200) NOT NULL,
                due_date DATETIME NOT NULL, estimated_hours FLOAT NOT NULL,
                created_at DATETIME NOT NULL
            );
            CREATE TABLE personal_task (
                id INTEGER PRIMARY KEY, title VARCHAR(200) NOT NULL,
                due_date DATETIME NOT NULL, details TEXT NOT NULL DEFAULT '',
                created_at DATETIME NOT NULL
            );
            INSERT INTO course VALUES (11,'CS 3398','2026-10-01');
            INSERT INTO assignment VALUES
                (22,11,'Project','2026-10-30',7,'2026-10-01');
            INSERT INTO personal_task VALUES
                (33,'Study','2026-10-20','Library','2026-10-01');
        """)
    return path


def test_migrates_without_losing_records(legacy_db):
    backup, user_id = migrate(legacy_db, "  Student@Example.edu ", "safe-password-123")
    assert backup.is_file()
    with sqlite3.connect(legacy_db) as con:
        con.execute("PRAGMA foreign_keys=ON")
        user = con.execute("SELECT email, password_hash FROM user WHERE id=?", (user_id,)).fetchone()
        assert user[0] == "student@example.edu"
        assert user[1] != "safe-password-123"
        assert check_password_hash(user[1], "safe-password-123")
        assert con.execute("SELECT id,name,user_id FROM course").fetchone() == (11, "CS 3398", user_id)
        assert con.execute("SELECT id,user_id,title,details FROM personal_task").fetchone() == (
            33, user_id, "Study", "Library"
        )
        assert con.execute("SELECT id,course_id,estimated_hours FROM assignment").fetchone() == (
            22, 11, 7.0
        )
        assert not con.execute("PRAGMA foreign_key_check").fetchall()
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("INSERT INTO course (user_id,name,created_at) VALUES (999,'Invalid','2026-10-01')")
        con.rollback()
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("INSERT INTO course (name,created_at) VALUES ('Missing Owner','2026-10-01')")
    with sqlite3.connect(backup) as con:
        assert con.execute("SELECT name FROM course WHERE id=11").fetchone()[0] == "CS 3398"
        assert "user_id" not in [column[1] for column in con.execute("PRAGMA table_info(course)")]


def test_refuses_double_migration(legacy_db):
    migrate(legacy_db, "first@example.edu", "safe-password-123")
    with pytest.raises(ValueError, match="already exist"):
        migrate(legacy_db, "second@example.edu", "safe-password-123")


def test_validation_prevents_changes(legacy_db):
    with pytest.raises(ValueError, match="Password"):
        migrate(legacy_db, "valid@example.edu", "short")
    with pytest.raises(ValueError, match="email"):
        migrate(legacy_db, "invalid", "safe-password-123")
    with sqlite3.connect(legacy_db) as con:
        assert "user" not in {
            row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }


def test_email_normalization():
    assert normalize_email("  USER@EXAMPLE.EDU ") == "user@example.edu"
