"""StudyFlow courses UI with previews for upcoming features."""
import os
from flask import Flask, flash, redirect, render_template, request, url_for
from sqlalchemy import event
from sqlalchemy.engine import Engine
from models import db, Course


@event.listens_for(Engine, "connect")
def enable_sqlite_foreign_keys(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("STUDYFLOW_DATABASE_URI", "sqlite:///studyflow.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SECRET_KEY"] = os.environ.get("STUDYFLOW_SECRET_KEY", "dev-secret-key")
db.init_app(app)
with app.app_context():
    db.create_all()


@app.route("/")
def index():
    return render_template("index.html", courses=Course.query.order_by(Course.name).all())


@app.route("/add-course", methods=["GET", "POST"])
def add_course():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name or len(name) > 100:
            flash("Enter a course name between 1 and 100 characters.")
            return render_template("add_course.html"), 400
        db.session.add(Course(name=name))
        db.session.commit()
        return redirect(url_for("index"))
    return render_template("add_course.html")


@app.route("/add-assignment")
def add_assignment():
    return render_template("coming_soon.html", feature="Add Assignment")


@app.route("/schedule")
def schedule():
    return render_template("coming_soon.html", feature="Schedule")


@app.route("/connect-canvas")
def connect_canvas():
    return render_template("coming_soon.html", feature="Connect Canvas")


if __name__ == "__main__":
    app.run(debug=True, port=5001)
