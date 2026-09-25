# StudyFlow — Smart Study Planner

A tool that generates a personalized study schedule using spaced repetition.

## Prerequisites
- Python 3.9+
- Git

## Setup

1. Clone the repo:
   git clone https://github.com/Dhiraj02929130/StudyFlow_Team01.git
   cd StudyFlow_Team01

2. Create and activate a virtual environment:
   python3 -m venv .venv
   source .venv/bin/activate

3. Install dependencies:
   pip install -r requirements.txt

4. Run the app:
   python app.py

Open http://127.0.0.1:5001 to see `Hello, StudyFlow!`.

## SQLite initialization

Starting the app automatically creates `studyflow.db` in the project directory
if it does not exist. `models.py` defines the shared SQLAlchemy helper, and
`app.py` configures SQLite and calls `db.create_all()` inside an application
context. There are no course or assignment tables yet; those belong to later
model work. Restarting the app preserves existing database contents.

The database and virtual environment are excluded by `.gitignore`.

To verify creation without affecting an existing database, use a fresh copy of
this setup branch, install the requirements, and run `python app.py`. Confirm
that `studyflow.db` appears and the hello-world page loads. Stop and restart the
app to confirm startup works with an existing database. Do not delete an
existing database containing work just to repeat this check.

## Status
Sprint 1 — Project skeleton with hello world route.
