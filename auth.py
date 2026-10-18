"""StudyFlow account registration and session authentication."""
import secrets
from functools import wraps

from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash
from sqlalchemy.exc import IntegrityError

from models import db, User

auth = Blueprint("auth", __name__)


def current_user():
    if not hasattr(g, "current_user"):
        user_id = session.get("user_id")
        g.current_user = db.session.get(User, user_id) if isinstance(user_id, int) else None
        if user_id and g.current_user is None:
            session.clear()
    return g.current_user


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if current_user() is None:
            return redirect(url_for("auth.login"))
        return view(*args, **kwargs)
    return wrapped


def csrf_token():
    if "_csrf_token" not in session:
        session["_csrf_token"] = secrets.token_urlsafe(32)
    return session["_csrf_token"]


@auth.before_app_request
def protect_requests():
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        received = request.form.get("_csrf_token", "")
        if not received or not secrets.compare_digest(received, csrf_token()):
            abort(400, description="Invalid CSRF token.")


@auth.app_context_processor
def inject_auth():
    return {"current_user": current_user(), "csrf_token": csrf_token}


@auth.route("/register", methods=["GET", "POST"])
def register():
    if current_user():
        return redirect(url_for("index"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        if not email or len(email) > 255 or "@" not in email:
            flash("Enter a valid email address.")
        elif len(password) < 12:
            flash("Use a password with at least 12 characters.")
        elif password != confirm:
            flash("Passwords do not match.")
        elif User.query.filter_by(email=email).first():
            flash("An account with that email already exists.")
        else:
            user = User(email=email, password_hash=generate_password_hash(password))
            db.session.add(user)
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                flash("An account with that email already exists.")
            else:
                session.clear()
                session["user_id"] = user.id
                session["_csrf_token"] = secrets.token_urlsafe(32)
                return redirect(url_for("index"))
        return render_template("register.html"), 400
    return render_template("register.html")


@auth.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("index"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()
        if user is None or not check_password_hash(user.password_hash, password):
            flash("Invalid email or password.")
            return render_template("login.html"), 401
        session.clear()
        session["user_id"] = user.id
        session["_csrf_token"] = secrets.token_urlsafe(32)
        return redirect(url_for("index"))
    return render_template("login.html")


@auth.route("/logout", methods=["POST"])
@login_required
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
