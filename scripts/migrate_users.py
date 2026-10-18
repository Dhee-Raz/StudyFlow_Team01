"""Safely migrate the original StudyFlow SQLite schema to per-user ownership.

Run BEFORE starting the updated Flask application on an existing database:
    python scripts/migrate_users.py --database instance/studyflow.db --legacy-email student@example.edu

The account email is explicit; the password is read securely from a prompt.
A timestamped SQLite backup is created before any changes.
"""
import argparse
from datetime import datetime, timezone
from getpass import getpass
from pathlib import Path
import shutil
import sqlite3
import sys

from werkzeug.security import generate_password_hash


def normalize_email(email):
    normalized = email.strip().lower()
    if not normalized or len(normalized) > 255 or "@" not in normalized:
        raise ValueError("Provide a valid account email (maximum 255 characters).")
    return normalized


def migrate(database_path, owner_email, password):
    """Return (backup_path, owner_id). Preserve records or roll back on failure."""
    path = Path(database_path).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"Database not found: {path}; check the path before migrating.")
    if not password or len(password) < 8:
        raise ValueError("Password must contain at least 8 characters.")
    email = normalize_email(owner_email)

    with sqlite3.connect(str(path)) as con:
        con.execute("PRAGMA foreign_keys=ON")
        tables = {row[0] for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        if not {"course", "assignment", "personal_task"}.issubset(tables):
            raise ValueError("Expected legacy course, assignment and personal_task tables.")

        for name in ("course", "personal_task"):
            columns = {row[1] for row in con.execute(f"PRAGMA table_info({name})")}
            if "user_id" in columns:
                raise ValueError("Ownership columns already exist; migration may have run.")
            expected = ({"id", "name", "created_at"} if name == "course"
                        else {"id", "title", "due_date", "details", "created_at"})
            if columns != expected:
                raise ValueError(f"Unexpected {name} columns: {columns}; refusing to drop columns.")
            custom = list(con.execute(
                "SELECT name FROM sqlite_master WHERE tbl_name=? "
                "AND type IN ('trigger','index') AND name NOT LIKE 'sqlite_autoindex_%'",
                (name,),
            ))
            if custom:
                raise ValueError(f"Custom indexes/triggers on {name}; manual migration required.")

        if "user" in tables and con.execute("SELECT 1 FROM user LIMIT 1").fetchone():
            raise ValueError("Existing users found; choose an explicit existing-user migration.")
        broken = con.execute(
            "SELECT COUNT(*) FROM assignment AS a LEFT JOIN course AS c "
            "ON a.course_id=c.id WHERE c.id IS NULL"
        ).fetchone()[0]
        if broken:
            raise ValueError("Orphaned assignments detected; fix them before migration.")

        backup = path.with_name(path.name + ".pre-users-" +
                                datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".bak")
        if backup.exists():
            raise ValueError(f"Backup already exists: {backup}")
        # SQLite backup API also handles journal/WAL state.
        with sqlite3.connect(str(backup)) as destination:
            con.backup(destination)

    try:
        # Foreign keys must be disabled before BEGIN for SQLite table rebuilding.
        with sqlite3.connect(str(path)) as con:
            con.execute("PRAGMA foreign_keys=OFF")
            con.execute("BEGIN IMMEDIATE")
            con.execute(
                "CREATE TABLE IF NOT EXISTS user ("
                "id INTEGER PRIMARY KEY, email VARCHAR(255) NOT NULL UNIQUE, "
                "password_hash VARCHAR(255) NOT NULL, created_at DATETIME NOT NULL)"
            )
            if con.execute("SELECT COUNT(*) FROM user").fetchone()[0]:
                raise ValueError("Existing users found; migration aborted.")
            created = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" ")
            cursor = con.execute(
                "INSERT INTO user (email,password_hash,created_at) VALUES (?,?,?)",
                (email, generate_password_hash(password), created),
            )
            owner_id = cursor.lastrowid
            before_courses = con.execute("SELECT COUNT(*) FROM course").fetchone()[0]
            before_tasks = con.execute("SELECT COUNT(*) FROM personal_task").fetchone()[0]
            before_assignments = con.execute("SELECT COUNT(*) FROM assignment").fetchone()[0]

            con.execute(
                "CREATE TABLE course_new (id INTEGER PRIMARY KEY, "
                "user_id INTEGER NOT NULL REFERENCES user(id), "
                "name VARCHAR(100) NOT NULL, created_at DATETIME NOT NULL)"
            )
            con.execute(
                "INSERT INTO course_new (id,user_id,name,created_at) "
                "SELECT id, ?, name, created_at FROM course", (owner_id,)
            )
            con.execute(
                "CREATE TABLE personal_task_new (id INTEGER PRIMARY KEY, "
                "user_id INTEGER NOT NULL REFERENCES user(id), "
                "title VARCHAR(200) NOT NULL, due_date DATETIME NOT NULL, "
                "details TEXT NOT NULL, created_at DATETIME NOT NULL)"
            )
            con.execute(
                "INSERT INTO personal_task_new "
                "(id,user_id,title,due_date,details,created_at) "
                "SELECT id, ?, title,due_date,details,created_at FROM personal_task",
                (owner_id,),
            )
            con.execute("DROP TABLE course")
            con.execute("ALTER TABLE course_new RENAME TO course")
            con.execute("DROP TABLE personal_task")
            con.execute("ALTER TABLE personal_task_new RENAME TO personal_task")
            con.execute("CREATE INDEX ix_course_user_id ON course(user_id)")
            con.execute("CREATE INDEX ix_personal_task_user_id ON personal_task(user_id)")

            if (con.execute("SELECT COUNT(*) FROM course").fetchone()[0] != before_courses
                or con.execute("SELECT COUNT(*) FROM personal_task").fetchone()[0] != before_tasks
                or con.execute("SELECT COUNT(*) FROM assignment").fetchone()[0] != before_assignments):
                raise ValueError("Record counts changed; rolling back migration.")
            if con.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("Foreign-key check failed; rolling back migration.")
            con.commit()
            con.execute("PRAGMA foreign_keys=ON")
            if con.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("Post-migration foreign-key check failed.")
            return backup, owner_id
    except Exception:
        # If post-commit verification fails, recover using the retained backup manually.
        print(f"Migration did not finish successfully. Original backup: {backup}", file=sys.stderr)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, help="Path to existing SQLite .db")
    parser.add_argument("--legacy-email", required=True, help="Account owning existing records")
    args = parser.parse_args()
    password = getpass("Password for the legacy account (not echoed): ")
    confirmation = getpass("Confirm password: ")
    if password != confirmation:
        parser.error("Passwords did not match.")
    try:
        backup, owner_id = migrate(args.database, args.legacy_email, password)
    except (ValueError, sqlite3.Error) as error:
        parser.exit(1, f"Migration failed: {error}\n")
    print(f"Migration successful; legacy owner ID: {owner_id}")
    print(f"Backup: {backup}")


if __name__ == "__main__":
    main()
