from flask import Flask
from sqlalchemy import event
from sqlalchemy.engine import Engine
from models import db, Course, Assignment


@event.listens_for(Engine, "connect")
def enable_sqlite_foreign_keys(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///studyflow.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)

with app.app_context():
    db.create_all()

@app.route("/")
def hello():
    return "Hello, StudyFlow!"

if __name__ == "__main__":
    app.run(debug=True)