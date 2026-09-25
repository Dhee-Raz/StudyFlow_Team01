# StudyFlow — Courses Home Screen

Initial Flask UI with a Courses navigation link and a friendly empty state.
This branch contains the home screen only. Course storage and forms will be added in later changes.

## Run locally

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5002.

## Code map

- `app.py`: Flask setup and the home route.
- `templates/base.html`: page shell, logo, and navigation.
- `templates/index.html`: Courses heading and empty state.
- `static/style.css`: theme, layout, navigation, and empty-state styling.
- `static/*.png`: logo and browser icons.

The server renders this page without querying a database.
