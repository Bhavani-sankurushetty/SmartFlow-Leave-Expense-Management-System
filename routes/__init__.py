"""SmartFlow Frontend Blueprints Package."""

from routes.main import main_bp
from routes.employee import employee_bp
from routes.approvals import approvals_bp
from routes.finance import finance_bp
from routes.admin import admin_bp
from routes.api import api_bp

__all__ = [
    "main_bp",
    "employee_bp",
    "approvals_bp",
    "finance_bp",
    "admin_bp",
    "api_bp",
]
