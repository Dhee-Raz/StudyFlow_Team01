# StudyFlow — Smart Study Planner

A tool that generates a personalized study schedule using spaced repetition.

## Prerequisites
- Python 3.8+
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
