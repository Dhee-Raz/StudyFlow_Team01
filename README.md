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

## Ticket 9 — Connect Canvas

Open **Connect Canvas**, enter your school’s HTTPS Canvas URL and personal access
token, and select **Verify and Connect**. Create a token in Canvas under
**Account → Settings → Approved Integrations → New Access Token**. School policy
may disable personal tokens. Use a short expiration and never share the token.

This ticket only verifies `GET /api/v1/users/self/profile`; it does not import or
modify courses, assignments, or schedules. API reference:
https://developerdocs.instructure.com/services/canvas/resources/users

Security and current scope:

- Tokens stay in server memory, isolated by an opaque browser-session identifier.
  They are not placed in cookies, the database, rendered pages, or application logs.
- Connections expire after one hour (expired entries are pruned on the next store
  access), on server restart, or immediately when disconnected. Closing a browser
  does not immediately remove the server entry. Disconnect also leaves planner data
  untouched; revoke the token in Canvas separately if needed.
- Failed reconnects leave a previously verified connection intact.
- Hosted `*.instructure.com` school URLs are supported. For a trusted custom Canvas
  domain, the administrator can set `STUDYFLOW_CANVAS_ALLOWED_HOSTS` to a comma-separated
  list of exact hostnames. Only configure real Canvas hosts; never internal services.
- Requests use HTTPS, a timeout, and no redirects. Connection/disconnection forms
  check CSRF tokens; connection responses use `Cache-Control: no-store`.
- Current `main` has no account authentication. This is a single-process development
  implementation, not persistent per-user credential storage. Before multi-worker
  or multi-user deployment, integrate the team's login system and an encrypted,
  shared credential store. Set a private `STUDYFLOW_SECRET_KEY` and serve over HTTPS.

Install with `pip install -r requirements-dev.txt` and run `python -m pytest -q`.
Canvas tests mock the API; they need no personal token and make no live requests.

## Ticket 10 — Import Canvas courses and assignments

After connecting, choose **Import Courses and Assignments**. StudyFlow fetches your
active student courses and their assignments, including paginated results, then saves
them to the existing Courses view. The import summary reports courses/assignments
added, skipped items, invalid items, and assignments needing a deadline.

- Titles, available deadlines, points, and safe Canvas assignment links are imported.
- Canvas profile timezone is used for deadline display (UTC if absent or invalid).
  No deadline is stored as `NULL`, clearly marked for review, and excluded from calendar
  dates and scheduling until a date is provided.
- Estimated study hours start at **0**; importing never requires manual course or
  assignment entry. Use **Review deadline and study hours** to enter an estimate in
  Calendar. Scheduling starts only after a deadline and positive estimate exist.
- Duplicate keys include Canvas host, Canvas account ID, course ID, and assignment ID.
  Reimporting skips existing records and preserves their local edits and hours.
  Refreshing changed deadlines and syncing are **Ticket 11**, not implemented here.
- All API pages are fetched before saving. HTTP/parse/pagination failures save nothing;
  a database save failure rolls back the whole import. Malformed individual items are
  counted as failed while valid items can be imported. Individual fields longer than
  the existing model limits are truncated to 100 characters for courses and 200 for titles.
- Redirects and pagination links outside the original HTTPS endpoint are rejected;
  network requests are bounded by timeouts and a 100-page limit per endpoint.
- Existing database tables are upgraded automatically at startup with an atomic,
  repeatable SQLite migration. It adds import metadata/unique indexes and allows
  missing assignment deadlines while preserving records and indexes. **Back up your
  SQLite database before upgrading.** No personal databases or tokens are committed.
- This remains a **single-user local planner**. Imported IDs are separated by Canvas
  account to avoid collisions, but the planner has no login/access-control layer and
  its course listing is shared. Do not deploy it as a shared multi-user service until
  the team's authentication/ownership work is integrated. Ticket 9's connection
  lifetime and single-process restrictions still apply.

### Try this branch

```bash
git fetch origin
git switch --track origin/feature/ticket-10-canvas-import
pip install -r requirements-dev.txt
python -m pytest -q
python app.py
```

Open http://127.0.0.1:5001/connect-canvas, verify your token, and import. Inspect Courses,
open an assignment in Canvas, then use its review link to add hours. Repeat the import
and verify no duplicates are added. Disconnecting does not delete imported records.

API references: [courses](https://developerdocs.instructure.com/services/canvas/resources/courses),
[assignments](https://developerdocs.instructure.com/services/canvas/resources/assignments),
[pagination](https://developerdocs.instructure.com/services/canvas/basics/file.pagination).
Automated tests use fake HTTP responses; a real school-token smoke test is still needed.
