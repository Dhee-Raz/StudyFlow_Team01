"""Idempotent SQLite upgrade for existing Ticket 9 databases."""
import re


def upgrade_canvas_import(engine):
    if engine.dialect.name != 'sqlite':
        raise RuntimeError('Canvas import migrations currently support SQLite only.')
    with engine.connect() as connection:
        # Explicit BEGIN makes SQLite DDL and copied data one atomic operation.
        connection.exec_driver_sql('BEGIN IMMEDIATE')
        try:
            additions = {
                'course': {'canvas_key': 'VARCHAR(600)'},
                'assignment': {'canvas_missing': 'BOOLEAN NOT NULL DEFAULT 0', 'canvas_key': 'VARCHAR(650)', 'canvas_url': 'VARCHAR(600)',
                               'canvas_timezone': 'VARCHAR(100)', 'points_possible': 'FLOAT'},
            }
            for table, fields in additions.items():
                columns = {row[1] for row in connection.exec_driver_sql(f'PRAGMA table_info({table})')}
                for field, definition in fields.items():
                    if field not in columns:
                        connection.exec_driver_sql(f'ALTER TABLE {table} ADD COLUMN {field} {definition}')
            info = connection.exec_driver_sql('PRAGMA table_info(assignment)').all()
            if any(row[1] == 'due_date' and row[3] for row in info):
                schema = connection.exec_driver_sql("SELECT sql FROM sqlite_master WHERE type='table' AND name='assignment'").scalar_one()
                objects = connection.exec_driver_sql("SELECT sql FROM sqlite_master WHERE tbl_name='assignment' AND type IN ('index', 'trigger') AND sql IS NOT NULL").scalars().all()
                schema = re.sub(r'CREATE TABLE\s+["`\[]?assignment["`\]]?', 'CREATE TABLE assignment_canvas_upgrade', schema, count=1, flags=re.I)
                schema, changes = re.subn(r'(\bdue_date\b\s+DATETIME)\s+NOT NULL', r'\1', schema, count=1, flags=re.I)
                if changes != 1:
                    raise RuntimeError('Unrecognized assignment schema; no migration was applied.')
                connection.exec_driver_sql(schema)
                connection.exec_driver_sql('INSERT INTO assignment_canvas_upgrade SELECT * FROM assignment')
                connection.exec_driver_sql('DROP TABLE assignment')
                connection.exec_driver_sql('ALTER TABLE assignment_canvas_upgrade RENAME TO assignment')
                for statement in objects:
                    connection.exec_driver_sql(statement)
            connection.exec_driver_sql('CREATE UNIQUE INDEX IF NOT EXISTS ux_course_canvas_key ON course(canvas_key)')
            connection.exec_driver_sql('CREATE UNIQUE INDEX IF NOT EXISTS ux_assignment_canvas_key ON assignment(canvas_key)')
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def upgrade_study_availability(engine):
    """Idempotently add per-user preferences without altering existing user rows.

    Use SQLite's transaction to ensure both ALTER statements either commit together
    or are rolled back. Defaults cover accounts created before Sprint 2 Ticket 4.
    """
    if engine.dialect.name != "sqlite":
        raise RuntimeError("Study availability migration currently supports SQLite only.")
    with engine.connect() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            existing = {column[1] for column in
                        connection.exec_driver_sql("PRAGMA table_info(user)")}
            if not existing:
                raise RuntimeError("The user table must exist before upgrading availability.")
            if "available_weekdays" not in existing:
                connection.exec_driver_sql(
                    "ALTER TABLE user ADD COLUMN available_weekdays VARCHAR(13) "
                    "NOT NULL DEFAULT '0,1,2,3,4,5,6'"
                )
            if "daily_study_hours" not in existing:
                connection.exec_driver_sql(
                    "ALTER TABLE user ADD COLUMN daily_study_hours FLOAT NOT NULL DEFAULT 4.0"
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
