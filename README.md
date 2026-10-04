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
