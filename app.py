from flask import Flask, render_template, request

app = Flask(__name__)

@app.route("/")
def hello():
    return "Hello, StudyFlow!"

@app.route("/add-course", methods=["GET", "POST"])
def add_course():

    error = None

    if request.method == "POST":
        course_name = request.form.get("name", "").strip()

        if not course_name:
            error = "Course name is required."
        else:
            return f"Course submitted: {course_name}"

    return render_template("add_course.html", error=error)

if __name__ == "__main__":
    app.run(debug=True)

