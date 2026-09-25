"""Minimal Flask application serving the Courses home screen."""

from flask import Flask, render_template

app = Flask(__name__)


@app.route("/")
def index():
    """Display the initial Courses page before course management is connected."""
    return render_template("index.html")


if __name__ == "__main__":
    app.run(debug=True, port=5002)
