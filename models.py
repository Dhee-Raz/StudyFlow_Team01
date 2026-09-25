"""Shared SQLAlchemy object; course and assignment models will be added later."""

from flask_sqlalchemy import SQLAlchemy

# Create the database helper here so future models can import it without
# importing app.py and creating a circular dependency.
db = SQLAlchemy()
