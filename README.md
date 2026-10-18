# StudyFlow — Smart Study Planner

A tool that generates a personalized study schedule using spaced repetition.

## Prerequisites
- Python 3.9+
- Git

## Setup

1. Clone the repo:
   git clone https://github.com/Dhee-Raz/StudyFlow_Team01.git
   cd StudyFlow_Team01

2. Create and activate a virtual environment:
   python3 -m venv .venv
   source .venv/bin/activate

3. Install dependencies:
   pip install -r requirements.txt

4. Run the app:
   python app.py

## Features

Run `python app.py` and open http://127.0.0.1:5001.

- Add courses and assignments with due dates and estimated study hours.
- View academic deadlines and personal responsibilities in the monthly calendar.
- Select and edit calendar items; date changes move items to their new day.
- View generated study sessions on the Schedule page.
- Connect Canvas is not available yet.

## Tests

Install the test dependencies with `pip install -r requirements-dev.txt`, then run
`python -m pytest` from the repository root.


## Sprint 1 demo (Ticket 8)

With the app running at http://127.0.0.1:5001, complete this flow in one browser tab:

1. Open Courses, choose Add Course, enter `CS 3398`, and save.
2. Choose Add Assignment and select that course.
3. Enter `Sprint 1 Report`, choose a due date seven days from today,
   and enter `7` estimated study hours.
4. Submit. The app redirects to Schedule automatically.
5. Confirm seven dated sessions, each showing the assignment, course, and
   `1.00 hours`. Sessions start today and finish the day before the deadline.
6. Open Courses to confirm the assignment also appears under its course.

No shell commands or manual database edits are needed to add demo records.
Existing assignments may add more sessions to the page; use a distinct title
when repeating the demo. After opening the app, this is a short walkthrough
suitable for a 30-second sprint demonstration.

`tests/test_integration.py` verifies the forms, SQLite persistence, scheduler,
and rendered schedule together using one HTTP client and an isolated temporary
database. It also checks invalid submissions and the no-course guidance.


## Sprint 2 Ticket 1 — account database foundation

The new `User` model stores an ID, normalized unique email address, password hash
and creation timestamp. Courses and personal tasks have required `user_id`
foreign keys; assignments continue to belong to a user through their course.

**Important:** This ticket implements the data layer only. Registration/login,
per-user access control, and updating UI routes to select the signed-in owner
are Tickets 2 and 3. Until those tickets are integrated, the pre-authentication
course/task creation routes cannot insert into the new non-nullable owner
columns. Do not deploy this branch as a complete multi-user application.

### Existing SQLite databases: migrate before using the new code

1. Stop Flask. Keep your existing database safe; never delete `instance/`.
2. Install the requirements in your Python virtual environment.
3. Select the **specific account** that should own legacy course and task data.
   Run from the repository root, using the actual database file path:

   ```bash
   python scripts/migrate_users.py --database instance/studyflow.db --legacy-email student@example.edu
   ```

4. At the prompts, enter and confirm a password. It is never written to shell
   history or stored in plaintext; only a salted password hash is stored.
5. Verify the command reports success and a
   `studyflow.db.pre-users-<timestamp>.bak` backup. The migration preserves
   course, assignment and personal-task records (including original IDs).
6. On a fresh database, no migration is required; SQLAlchemy creates the
   new tables using the updated models.

This migration intentionally refuses ambiguous or unexpected schemas, repeated
migrations, orphaned assignments, and databases with custom course/task
indexes or triggers. Resolve those cases manually rather than risk data loss.
It uses SQLite's backup API and a transactional table rebuild; do not run it
concurrently with a live application. Existing database files and backups
must stay out of Git.

### Ticket 1 verification

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q tests/test_user_migration.py
python -m pytest -q
```

The first test verifies record preservation, password hashes, the ownership
foreign keys, a usable backup and rejection of a second migration. The other
Sprint 1 tests must continue to pass after later tickets update their fixture
setup for authenticated users.

## Sprint 2 Ticket 2 — Registration and login

Ticket 2 builds on the Sprint 2 Ticket 1 user/ownership database model.
**Migrate an existing database before running this branch** using the
Ticket 1 instructions above. The two pull requests should be reviewed
and integrated together because this branch is based on Ticket 1.

- Register at `/register` using an email address and a password of at
  least 12 characters. Email is normalized before storage. Passwords
  are stored as salted hashes, never plaintext.
- Log in at `/login`; log out using the navigation form.
- The planner requires login. Each course and personal task belongs to
  the signed-in account; assignment ownership follows its course.
- All state-changing forms require an anti-CSRF token. Missing or
  invalid tokens return HTTP 400.
- The existing course, assignment, and calendar routes now scope queries,
  edits, and deletions by account ID.

### Test

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q tests/test_authentication.py
python -m pytest -q tests/test_integration.py
python -m pytest -q tests/test_scheduler.py tests/test_user_migration.py
```

**Security note:** Set `STUDYFLOW_SECRET_KEY` to a long random value
before hosting publicly. The fallback key is for local development only;
never commit production credentials. Authentication is not a substitute
for HTTPS, secure session-cookie deployment configuration, rate limiting,
or production security review.
