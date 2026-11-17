"""Course materials library (PDFs + plain-text notes) and assignment memos.

Every query filters by the signed-in user, so another student's course,
file, note, or memo is a 404 rather than a permission error.
"""
import re
import uuid
from datetime import date
from pathlib import Path

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)
from werkzeug.exceptions import RequestEntityTooLarge

from auth import current_user, login_required
from models import (
    Assignment,
    AssignmentMemo,
    Course,
    CourseMaterial,
    CourseNote,
    db,
)

library = Blueprint("library", __name__, url_prefix="/library")

MAX_PDF_BYTES = 10 * 1024 * 1024  # 10 MB per PDF
MAX_TITLE_CHARS = 200
MAX_NOTE_CHARS = 20000
MAX_MEMO_CHARS = 5000
MAX_SEARCH_CHARS = 100
STORED_NAME = re.compile(r"^[0-9a-f]{32}\.pdf$")


# ---------------------------------------------------------------- helpers

def upload_dir():
    """Folder for uploaded PDFs. Lives under instance/, never under static/."""
    configured = current_app.config.get("STUDYFLOW_UPLOAD_DIR")
    path = Path(configured) if configured else Path(current_app.instance_path) / "uploads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def remove_stored_files(stored_filenames):
    """Delete PDFs from disk. Safe to call with names whose files are already gone."""
    folder = upload_dir()
    for name in stored_filenames:
        if not STORED_NAME.match(name or ""):
            continue  # only ever touch names this module generated
        try:
            (folder / name).unlink()
        except FileNotFoundError:
            pass
        except OSError:
            current_app.logger.warning("Could not delete uploaded file %s", name)


def _owned_course(course_id):
    return Course.query.filter_by(
        id=course_id, user_id=current_user().id
    ).first_or_404()


def _owned_material(material_id):
    return CourseMaterial.query.filter_by(
        id=material_id, user_id=current_user().id
    ).first_or_404()


def _owned_note(note_id):
    return CourseNote.query.filter_by(
        id=note_id, user_id=current_user().id
    ).first_or_404()


def _like(term):
    """Build a LIKE pattern, escaping %, _ and \\ so they match literally."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _search_term():
    return request.args.get("q", "").strip()[:MAX_SEARCH_CHARS]


def _back_to_course(course_id):
    return redirect(url_for("library.course_library", course_id=course_id))


# ------------------------------------------------------------ library pages

@library.route("/")
@login_required
def library_home():
    """All of the student's courses, plus search across course/document/note titles."""
    user = current_user()
    q = _search_term()
    courses = Course.query.filter_by(user_id=user.id).order_by(Course.name).all()

    results = None
    if q:
        pattern = _like(q)
        results = {
            "courses": [c for c in courses if q.lower() in c.name.lower()],
            "materials": CourseMaterial.query.filter(
                CourseMaterial.user_id == user.id,
                CourseMaterial.title.ilike(pattern, escape="\\"),
            ).order_by(CourseMaterial.title).all(),
            "notes": CourseNote.query.filter(
                CourseNote.user_id == user.id,
                CourseNote.title.ilike(pattern, escape="\\"),
            ).order_by(CourseNote.title).all(),
        }
    return render_template("library_home.html", courses=courses, q=q, results=results)


@library.route("/course/<int:course_id>")
@login_required
def course_library(course_id):
    """One course's materials library: PDFs and notes, optionally filtered by title."""
    course = _owned_course(course_id)
    q = _search_term()

    materials = CourseMaterial.query.filter_by(
        course_id=course.id, user_id=current_user().id
    )
    notes = CourseNote.query.filter_by(course_id=course.id, user_id=current_user().id)
    if q:
        pattern = _like(q)
        materials = materials.filter(CourseMaterial.title.ilike(pattern, escape="\\"))
        notes = notes.filter(CourseNote.title.ilike(pattern, escape="\\"))

    return render_template(
        "library_course.html",
        course=course,
        q=q,
        materials=materials.order_by(CourseMaterial.created_at.desc()).all(),
        notes=notes.order_by(CourseNote.updated_at.desc()).all(),
        max_pdf_mb=MAX_PDF_BYTES // (1024 * 1024),
    )


# ------------------------------------------------------------------- PDFs

@library.route("/course/<int:course_id>/upload", methods=["POST"])
@login_required
def upload_material(course_id):
    course = _owned_course(course_id)
    upload = request.files.get("file")
    title = request.form.get("title", "").strip()

    if upload is None or not upload.filename:
        flash("Choose a PDF to upload.")
        return _back_to_course(course.id)

    # Keep only the final path component of whatever the browser sent.
    original = Path(upload.filename.replace("\\", "/")).name
    if not original.lower().endswith(".pdf"):
        flash("Only PDF files can be uploaded.")
        return _back_to_course(course.id)

    # Read one byte past the limit so an oversized file is detected without
    # loading the whole thing.
    data = upload.stream.read(MAX_PDF_BYTES + 1)
    if len(data) > MAX_PDF_BYTES:
        flash(f"That PDF is too large. The limit is {MAX_PDF_BYTES // (1024 * 1024)} MB.")
        return _back_to_course(course.id)
    # Check the file's real contents, not just its name.
    if not data.startswith(b"%PDF-"):
        flash("That file is not a valid PDF.")
        return _back_to_course(course.id)

    if not title:
        title = Path(original).stem
    if len(title) > MAX_TITLE_CHARS:
        flash(f"Titles must be {MAX_TITLE_CHARS} characters or fewer.")
        return _back_to_course(course.id)

    stored = f"{uuid.uuid4().hex}.pdf"
    with open(upload_dir() / stored, "xb") as handle:
        handle.write(data)

    db.session.add(
        CourseMaterial(
            user_id=current_user().id,
            course_id=course.id,
            title=title,
            original_filename=original[:255],
            stored_filename=stored,
            size_bytes=len(data),
        )
    )
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        remove_stored_files([stored])  # don't leave an orphaned file behind
        raise

    flash("PDF uploaded.")
    return _back_to_course(course.id)


def _send_material(material_id, as_attachment):
    material = _owned_material(material_id)
    folder = upload_dir()
    if not STORED_NAME.match(material.stored_filename) or not (
        folder / material.stored_filename
    ).is_file():
        abort(404)
    response = send_from_directory(
        str(folder),
        material.stored_filename,
        mimetype="application/pdf",
        as_attachment=as_attachment,
        download_name=material.original_filename,
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "private, no-store"
    return response


@library.route("/material/<int:material_id>/view")
@login_required
def view_material(material_id):
    """Open the PDF in the browser."""
    return _send_material(material_id, as_attachment=False)


@library.route("/material/<int:material_id>/download")
@login_required
def download_material(material_id):
    return _send_material(material_id, as_attachment=True)


@library.route("/material/<int:material_id>/delete", methods=["POST"])
@login_required
def delete_material(material_id):
    material = _owned_material(material_id)
    course_id = material.course_id
    stored = material.stored_filename
    db.session.delete(material)
    db.session.commit()
    remove_stored_files([stored])  # after the commit, so a failed commit keeps the file
    flash("PDF deleted.")
    return _back_to_course(course_id)


# ------------------------------------------------------------------ notes

def _note_fields():
    """Return (title, body, error) from the submitted note form."""
    title = request.form.get("title", "").strip()
    body = request.form.get("body", "").strip()
    if not title or len(title) > MAX_TITLE_CHARS:
        return title, body, f"Enter a note title up to {MAX_TITLE_CHARS} characters."
    if len(body) > MAX_NOTE_CHARS:
        return title, body, f"Notes must be {MAX_NOTE_CHARS} characters or fewer."
    return title, body, None


@library.route("/course/<int:course_id>/notes", methods=["POST"])
@login_required
def add_note(course_id):
    course = _owned_course(course_id)
    title, body, error = _note_fields()
    if error:
        flash(error)
        return _back_to_course(course.id)
    db.session.add(
        CourseNote(user_id=current_user().id, course_id=course.id, title=title, body=body)
    )
    db.session.commit()
    flash("Note added.")
    return _back_to_course(course.id)


@library.route("/note/<int:note_id>", methods=["POST"])
@login_required
def update_note(note_id):
    note = _owned_note(note_id)
    title, body, error = _note_fields()
    if error:
        flash(error)
        return _back_to_course(note.course_id)
    note.title = title
    note.body = body
    db.session.commit()
    flash("Note updated.")
    return _back_to_course(note.course_id)


@library.route("/note/<int:note_id>/delete", methods=["POST"])
@login_required
def delete_note(note_id):
    note = _owned_note(note_id)
    course_id = note.course_id
    db.session.delete(note)
    db.session.commit()
    flash("Note deleted.")
    return _back_to_course(course_id)


# ---------------------------------------------------------- assignment memo

@library.route("/assignment/<int:assignment_id>/memo", methods=["POST"])
@login_required
def save_memo(assignment_id):
    """Create, update, or (when left blank) clear an assignment's memo."""
    assignment = Assignment.query.join(Course).filter(
        Assignment.id == assignment_id,
        Course.user_id == current_user().id,
    ).first_or_404()

    # Return to the calendar with this assignment's panel open.
    due = assignment.due_date.date() if assignment.due_date else date.today()
    year = request.form.get("return_year", type=int) or due.year
    month = request.form.get("return_month", type=int) or due.month
    back = redirect(
        url_for("calendar_view", year=year, month=month, selected=f"assignment-{assignment.id}")
    )

    body = request.form.get("memo", "").strip()
    if len(body) > MAX_MEMO_CHARS:
        flash(f"Memos must be {MAX_MEMO_CHARS} characters or fewer.")
        return back

    if not body:
        if assignment.memo is not None:
            db.session.delete(assignment.memo)
            db.session.commit()
            flash("Memo cleared.")
        return back

    if assignment.memo is None:
        assignment.memo = AssignmentMemo(body=body)
    else:
        assignment.memo.body = body
    db.session.commit()
    flash("Memo saved.")
    return back


# ------------------------------------------------------------ registration

def register_library(app):
    """Attach the blueprint and the upload size limit to the app."""
    # Rejects oversized requests before they are buffered. Slightly above the
    # PDF limit to leave room for the form fields around the file.
    if app.config.get("MAX_CONTENT_LENGTH") is None:
        app.config["MAX_CONTENT_LENGTH"] = MAX_PDF_BYTES + 1024 * 1024
    app.register_blueprint(library)

    @app.errorhandler(RequestEntityTooLarge)
    def upload_too_large(error):
        flash(f"That upload is too large. PDFs can be up to {MAX_PDF_BYTES // (1024 * 1024)} MB.")
        return redirect(url_for("library.library_home"))
