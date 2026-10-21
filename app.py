"""StudyFlow courses UI with previews for upcoming features."""
import calendar as month_calendar
from datetime import date, datetime, timedelta
import math
import os
from flask import Flask, flash, redirect, render_template, request, url_for
from auth import auth, current_user, login_required
from sqlalchemy import event
from sqlalchemy.engine import Engine
from models import db, Course, Assignment, PersonalTask
from scheduler import generate_study_sessions
from presentation import course_groups, schedule_sections
from canvas_connection import register_canvas
from migrations import upgrade_canvas_import

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
app.register_blueprint(auth)
with app.app_context():
    db.create_all()
    upgrade_canvas_import(db.engine)


@app.context_processor
def canvas_display_helpers():
    from flask import session
    store = app.extensions.get("canvas_connections")
    record = store.get(session.get("canvas_connection")) if store else None
    return {
        "canvas_user_name": record["profile"]["name"] if record else None,
        "canvas_assignment_url": lambda assignment: assignment.canvas_url,
    }


@app.route("/")
@login_required
def index():
    courses = Course.query.filter_by(user_id=current_user().id).order_by(Course.name).all()
    return render_template("index.html", course_data=course_groups(courses, date.today()))


@app.route("/add-course", methods=["GET", "POST"])
@login_required
def add_course():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name or len(name) > 100:
            flash("Enter a course name between 1 and 100 characters.")
            return render_template("add_course.html"), 400
        db.session.add(Course(name=name, user_id=current_user().id))
        db.session.commit()
        return redirect(url_for("index"))
    return render_template("add_course.html")


@app.route("/delete-course/<int:course_id>", methods=["POST"])
@login_required
def delete_course(course_id):
    course = Course.query.filter_by(id=course_id, user_id=current_user().id).first_or_404()
    # Remove dependents first to respect the required course foreign key.
    for assignment in list(course.assignments):
        db.session.delete(assignment)
    db.session.delete(course)
    db.session.commit()
    return redirect(url_for("index"))


@app.route("/add-assignment", methods=["GET", "POST"])
@login_required
def add_assignment():
    courses = Course.query.filter_by(user_id=current_user().id).order_by(Course.name).all()
    if not courses:
        if request.method == "POST":
            flash("Add a course before creating an assignment.")
            return redirect(url_for("add_course"))
        return render_template("add_assignment.html", courses=[], today=date.today().isoformat())

    if request.method == "POST":
        course_id = request.form.get("course_id", type=int)
        course = Course.query.filter_by(id=course_id, user_id=current_user().id).first() if course_id else None
        title = request.form.get("title", "").strip()
        due_date_value = request.form.get("due_date", "")
        estimated_hours_value = request.form.get("estimated_hours", "")
        errors = []

        if course is None:
            errors.append("Choose a course.")
        if not title or len(title) > 200:
            errors.append("Enter an assignment title up to 200 characters.")

        try:
            due_date = datetime.strptime(due_date_value, "%Y-%m-%d")
            if due_date.date() < date.today():
                errors.append("Choose a due date that is today or later.")
        except ValueError:
            due_date = None
            errors.append("Enter a valid due date.")

        try:
            estimated_hours = float(estimated_hours_value)
            if not math.isfinite(estimated_hours) or estimated_hours <= 0:
                errors.append("Estimated study hours must be greater than zero.")
        except ValueError:
            estimated_hours = None
            errors.append("Enter a valid number of estimated study hours.")

        if errors:
            for error in errors:
                flash(error)
            return render_template(
                "add_assignment.html",
                courses=courses,
                today=date.today().isoformat(),
                selected_course_id=course_id,
            ), 400

        db.session.add(
            Assignment(
                course=course,
                title=title,
                due_date=due_date,
                estimated_hours=estimated_hours,
            )
        )
        db.session.commit()
        flash("Assignment added. Your study schedule is ready.")
        return redirect(url_for("schedule"))

    return render_template("add_assignment.html", courses=courses, today=date.today().isoformat())


def _calendar_redirect(year=None, month=None, selected=None):
    today = date.today()
    return redirect(
        url_for(
            "calendar_view",
            year=year or today.year,
            month=month or today.month,
            selected=selected,
        )
    )


def _parse_calendar_date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


@app.route("/calendar")
@login_required
def calendar_view():
    today = date.today()
    try:
        year = int(request.args.get("year", today.year))
        month = int(request.args.get("month", today.month))
        if month < 1 or month > 12 or year < 2 or year > 9998:
            raise ValueError
    except (TypeError, ValueError, OverflowError):
        year, month = today.year, today.month

    weeks = month_calendar.Calendar(firstweekday=0).monthdatescalendar(year, month)
    visible_days = {day for week in weeks for day in week}
    calendar_days = {
        day: {"events": [], "academic_count": 0, "personal_count": 0}
        for day in visible_days
    }

    for assignment in Assignment.query.join(Course).filter(Course.user_id == current_user().id).order_by(Assignment.due_date).all():
        if assignment.due_date is None:
            continue
        due_day = assignment.due_date.date()
        if due_day in calendar_days:
            day_events = calendar_days[due_day]
            day_events["academic_count"] += 1
            day_events["events"].append({
                "key": f"assignment-{assignment.id}",
                "title": assignment.title,
                "kind": "academic",
                "subtitle": assignment.course.name,
            })

    for task in PersonalTask.query.filter_by(user_id=current_user().id).order_by(PersonalTask.due_date).all():
        due_day = task.due_date.date()
        if due_day in calendar_days:
            day_events = calendar_days[due_day]
            day_events["personal_count"] += 1
            day_events["events"].append({
                "key": f"personal-{task.id}",
                "title": task.title,
                "kind": "personal",
                "subtitle": "Personal",
            })

    for day_events in calendar_days.values():
        event_positions = {"academic": 0, "personal": 0}
        for event in day_events["events"]:
            event_positions[event["kind"]] += 1
            event["position"] = event_positions[event["kind"]]

    selected_key = request.args.get("selected", "")
    selected_source, separator, selected_id = selected_key.rpartition("-")
    selected_item = None
    if separator and selected_source in {"assignment", "personal"}:
        try:
            selected_id = int(selected_id)
            model = Assignment if selected_source == "assignment" else PersonalTask
            selected_item = (Assignment.query.join(Course).filter(Assignment.id == selected_id, Course.user_id == current_user().id).first() if selected_source == "assignment" else PersonalTask.query.filter_by(id=selected_id, user_id=current_user().id).first())
        except ValueError:
            selected_item = None
    if selected_item is None:
        selected_kind = None
        selected_key = ""
    else:
        selected_kind = "academic" if selected_source == "assignment" else "personal"

    first_day = date(year, month, 1)
    previous_month = first_day - timedelta(days=1)
    last_day = month_calendar.monthrange(year, month)[1]
    next_month = date.fromordinal(date(year, month, last_day).toordinal() + 1)

    return render_template(
        "calendar.html",
        year=year,
        month=month,
        month_label=first_day.strftime("%B %Y"),
        weeks=weeks,
        calendar_days=calendar_days,
        today=today,
        selected_key=selected_key,
        selected_kind=selected_kind,
        selected_item=selected_item,
        previous_month=previous_month,
        next_month=next_month,
    )


@app.route("/calendar/personal", methods=["POST"])
@login_required
def add_personal_task():
    title = request.form.get("title", "").strip()
    due_day = _parse_calendar_date(request.form.get("due_date"))
    details = request.form.get("details", "").strip()
    year = request.form.get("return_year", type=int)
    month = request.form.get("return_month", type=int)

    if not title or len(title) > 200:
        flash("Enter a personal task title up to 200 characters.")
        return _calendar_redirect(year, month)
    if due_day is None:
        flash("Enter a valid due date.")
        return _calendar_redirect(year, month)
    if len(details) > 1000:
        flash("Details must be 1000 characters or fewer.")
        return _calendar_redirect(year, month)

    task = PersonalTask(
        title=title,
        due_date=datetime.combine(due_day, datetime.min.time()),
        details=details,
        user_id=current_user().id,
    )
    db.session.add(task)
    db.session.commit()
    flash("Personal responsibility added to your calendar.")
    return _calendar_redirect(due_day.year, due_day.month)


@app.route("/calendar/personal/<int:task_id>/delete", methods=["POST"])
@login_required
def delete_personal_task(task_id):
    # Only fetch the task if it belongs to the logged-in user (404 otherwise)
    task = PersonalTask.query.filter_by(id=task_id, user_id=current_user().id).first_or_404()
    year = request.form.get("return_year", type=int)
    month = request.form.get("return_month", type=int)
    db.session.delete(task)
    db.session.commit()
    flash("Personal responsibility deleted.")
    return _calendar_redirect(year, month)


@app.route("/calendar/personal/<int:task_id>", methods=["POST"])
@login_required
def update_personal_task(task_id):
    # Only fetch the assignment if its course belongs to the logged-in user (404 otherwise)
    task = PersonalTask.query.filter_by(id=task_id, user_id=current_user().id).first_or_404()
    title = request.form.get("title", "").strip()
    due_day = _parse_calendar_date(request.form.get("due_date"))
    details = request.form.get("details", "").strip()

    if not title or len(title) > 200:
        flash("Enter a personal task title up to 200 characters.")
        return _calendar_redirect(selected=f"personal-{task.id}")
    if due_day is None:
        flash("Enter a valid due date.")
        return _calendar_redirect(selected=f"personal-{task.id}")
    if len(details) > 1000:
        flash("Details must be 1000 characters or fewer.")
        return _calendar_redirect(selected=f"personal-{task.id}")

    task.title = title
    task.due_date = datetime.combine(due_day, datetime.min.time())
    task.details = details
    db.session.commit()
    flash("Personal responsibility updated.")
    return _calendar_redirect(due_day.year, due_day.month, f"personal-{task.id}")


@app.route("/calendar/assignment/<int:assignment_id>", methods=["POST"])
@login_required
def update_calendar_assignment(assignment_id):
    assignment = Assignment.query.join(Course).filter(Assignment.id == assignment_id, Course.user_id == current_user().id).first_or_404()
    title = request.form.get("title", "").strip()
    due_day = _parse_calendar_date(request.form.get("due_date"))
    estimated_hours_value = request.form.get("estimated_hours", "")
    try:
        estimated_hours = float(estimated_hours_value)
    except ValueError:
        estimated_hours = None

    if not title or len(title) > 200:
        flash("Enter an assignment title up to 200 characters.")
        return _calendar_redirect(selected=f"assignment-{assignment.id}")
    if due_day is None:
        flash("Enter a valid due date.")
        return _calendar_redirect(selected=f"assignment-{assignment.id}")
    if estimated_hours is None or not math.isfinite(estimated_hours) or estimated_hours <= 0:
        flash("Estimated study hours must be greater than zero.")
        return _calendar_redirect(selected=f"assignment-{assignment.id}")

    assignment.title = title
    # Updating only study hours must preserve the imported deadline's time.
    if assignment.due_date is None or assignment.due_date.date() != due_day:
        assignment.due_date = datetime.combine(due_day, datetime.min.time())
    assignment.estimated_hours = estimated_hours
    db.session.commit()
    flash("Assignment updated.")
    return _calendar_redirect(due_day.year, due_day.month, f"assignment-{assignment.id}")


@app.route("/calendar/assignment/<int:assignment_id>/delete", methods=["POST"])
@login_required
def delete_calendar_assignment(assignment_id):
    assignment = Assignment.query.join(Course).filter(
        Assignment.id == assignment_id,
        Course.user_id == current_user().id,
    ).first_or_404()    
    year = request.form.get("return_year", type=int)
    month = request.form.get("return_month", type=int)
    db.session.delete(assignment)
    db.session.commit()
    flash("Assignment deleted.")
    return _calendar_redirect(year, month)


@app.route("/schedule")
@login_required
def schedule():
    assignments = Assignment.query.join(Course).filter(Course.user_id == current_user().id).all()
    return render_template("schedule.html", **schedule_sections(assignments, date.today()))



app.config["CANVAS_ALLOWED_HOSTS"] = tuple(
    host.strip().lower() for host in os.environ.get("STUDYFLOW_CANVAS_ALLOWED_HOSTS", "canvas.txstate.edu").split(",") if host.strip()
)
register_canvas(app)

@app.before_request
def protect_canvas_pages():
    if request.endpoint in {"connect_canvas", "disconnect_canvas", "import_canvas", "sync_canvas"} and current_user() is None:
        return redirect(url_for("auth.login"))


if __name__ == "__main__":
    app.run(debug=True, port=5001)
