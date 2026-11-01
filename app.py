"""SmartFlow Web Application Entry Point."""

from __future__ import annotations

import os
from flask import Flask, jsonify, render_template, session
from sqlalchemy import inspect, text

from config import Config, INSTANCE_DIR
from generator.constants import COUNTRY_CURRENCIES
from models import ApprovalStep, Employee, db
from routes import (
    admin_bp,
    api_bp,
    approvals_bp,
    employee_bp,
    finance_bp,
    main_bp,
)

app = Flask(__name__, instance_relative_config=True)
app.config.from_object(Config)
os.makedirs(app.instance_path, exist_ok=True)
os.makedirs(INSTANCE_DIR, exist_ok=True)

db.init_app(app)

# Register Blueprints
app.register_blueprint(main_bp)
app.register_blueprint(employee_bp)
app.register_blueprint(approvals_bp)
app.register_blueprint(finance_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(api_bp)


def init_database():
    """Create all application tables. Does not insert synthetic data."""
    os.makedirs(app.instance_path, exist_ok=True)
    os.makedirs(INSTANCE_DIR, exist_ok=True)
    with app.app_context():
        db_uri = str(app.config.get("SQLALCHEMY_DATABASE_URI", ""))
        if ("smartflow" in db_uri or "phase9" in db_uri) and not os.environ.get("FORCE_DB_INIT"):
            return
        db.create_all()


with app.app_context():
    init_database()


@app.context_processor
def inject_global_template_context():
    """Inject active user, pending tasks count, and role context into all Jinja templates."""
    user_id = session.get("user_id")
    current_user = None
    if user_id:
        current_user = Employee.query.filter_by(employee_id=user_id, is_active=True).first()

    if not current_user:
        # Default to a representative active employee (e.g. EMP-000002)
        current_user = Employee.query.filter_by(is_active=True).first()
        if current_user:
            session["user_id"] = current_user.employee_id

    pending_count = 0
    if current_user:
        pending_count = ApprovalStep.query.filter_by(
            approver_employee_id=current_user.employee_id,
            decision="pending",
        ).count()

    # Select representative employees for user switcher dropdown
    all_employees = (
        Employee.query.filter_by(is_active=True)
        .order_by(Employee.employee_id.asc())
        .limit(100)
        .all()
    )

    return {
        "current_user": current_user,
        "pending_approvals_count": pending_count,
        "active_employees": all_employees,
        "country_currencies": COUNTRY_CURRENCIES,
    }


@app.route("/health")
def health():
    """Application and database health check."""
    database_status = "disconnected"
    try:
        db.session.execute(text("SELECT 1"))
        inspector = inspect(db.engine)
        table_count = len(inspector.get_table_names())
        database_status = "connected" if table_count >= 0 else "disconnected"
    except Exception:
        database_status = "disconnected"

    return jsonify(
        {
            "application": "SmartFlow",
            "status": "ok",
            "database": database_status,
        }
    )


@app.errorhandler(404)
def not_found_error(error):
    return render_template("404.html"), 404


@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    return render_template("500.html"), 500


if __name__ == "__main__":
    init_database()
    app.run(debug=True, port=5000)
