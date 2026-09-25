"""Minimal Flask skeleton with automatic local SQLite initialization."""

from pathlib import Path

from flask import Flask

from models import db

BASE_DIR = Path(__file__).resolve().parent

app = Flask(__name__)
# Use an absolute path so launching from another folder uses the same database.
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{BASE_DIR / 'studyflow.db'}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)

# Open/create the database on startup and create any declared model tables.
# There are no application tables yet; later tickets will introduce models.
# Repeating create_all() preserves existing tables and their data.
with app.app_context():
    db.create_all()


@app.route("/")
def hello():
    """Confirm that the Flask application is running."""
    return "Hello, StudyFlow!"


if __name__ == "__main__":
    # Avoid the port 5000 conflict with macOS AirPlay Receiver.
    app.run(debug=True, port=5001)
