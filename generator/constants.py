"""Shared generation constants."""

from datetime import date

RANDOM_SEED = 20261022
REFERENCE_YEAR = 2026
REFERENCE_DATE = (2026, 10, 31)

EMPLOYEE_COUNT = 400
STARTER_EMPLOYEE_COUNT = 40
COST_CENTER_COUNT = 20
LEAVE_POLICY_COUNT = 6
LEAVE_REQUEST_COUNT = 800
STARTER_LEAVE_REQUEST_COUNT = 80
EXPENSE_CLAIM_COUNT = 1000
STARTER_EXPENSE_CLAIM_COUNT = 100
EXPENSE_LINES_MIN = 1
EXPENSE_LINES_MAX = 10

COUNTRIES = ("India", "USA", "UK", "Canada", "Australia")

COUNTRY_LOCALES = {
    "India": "en_IN",
    "USA": "en_US",
    "UK": "en_GB",
    "Canada": "en_CA",
    "Australia": "en_AU",
}

COUNTRY_CURRENCIES = {
    "India": "INR",
    "USA": "USD",
    "UK": "GBP",
    "Canada": "CAD",
    "Australia": "AUD",
}

DEPARTMENTS = (
    "Engineering",
    "Finance",
    "Human Resources",
    "Sales",
    "Marketing",
    "Operations",
    "Legal",
    "Product",
    "Customer Support",
    "Administration",
)

LEAVE_TYPES = (
    "Annual Leave",
    "Sick Leave",
    "Casual Leave",
    "Maternity Leave",
    "Paternity Leave",
    "Bereavement Leave",
)

EXPENSE_CATEGORIES = (
    "Travel",
    "Accommodation",
    "Meals",
    "Transportation",
    "Office Supplies",
    "Software",
    "Training",
)

AUTHORITY_LEVELS = {
    1: "Manager",
    2: "Department Head",
    3: "Finance",
    4: "Senior Management",
}

SCALES = {
    "starter": STARTER_EMPLOYEE_COUNT,
    "full": EMPLOYEE_COUNT,
}

TRANSACTION_SCALES = {
    "starter": (STARTER_LEAVE_REQUEST_COUNT, STARTER_EXPENSE_CLAIM_COUNT),
    "full": (LEAVE_REQUEST_COUNT, EXPENSE_CLAIM_COUNT),
}

COST_CENTER_BLUEPRINTS = (
    ("India", "Engineering", "CC-IND-ENG", 2_400_000),
    ("India", "Sales", "CC-IND-SAL", 1_100_000),
    ("India", "Operations", "CC-IND-OPS", 1_600_000),
    ("India", "Finance", "CC-IND-FIN", 900_000),
    ("USA", "Engineering", "CC-USA-ENG", 3_800_000),
    ("USA", "Marketing", "CC-USA-MKT", 1_500_000),
    ("USA", "Product", "CC-USA-PRD", 2_100_000),
    ("USA", "Legal", "CC-USA-LGL", 850_000),
    ("UK", "Engineering", "CC-UK-ENG", 2_900_000),
    ("UK", "Human Resources", "CC-UK-HR", 720_000),
    ("UK", "Finance", "CC-UK-FIN", 1_050_000),
    ("UK", "Customer Support", "CC-UK-CS", 980_000),
    ("Canada", "Sales", "CC-CAN-SAL", 1_250_000),
    ("Canada", "Operations", "CC-CAN-OPS", 1_400_000),
    ("Canada", "Product", "CC-CAN-PRD", 1_700_000),
    ("Canada", "Administration", "CC-CAN-ADM", 640_000),
    ("Australia", "Engineering", "CC-AUS-ENG", 2_200_000),
    ("Australia", "Marketing", "CC-AUS-MKT", 1_050_000),
    ("Australia", "Customer Support", "CC-AUS-CS", 870_000),
    ("Australia", "Human Resources", "CC-AUS-HR", 690_000),
)

LEAVE_POLICY_TEMPLATES = (
    {
        "leave_type": "Annual Leave",
        "country": "India",
        "annual_allowance_days": "18.00",
        "max_consecutive_days": "15.00",
        "min_notice_days": 14,
        "carryover_days": "8.00",
    },
    {
        "leave_type": "Sick Leave",
        "country": "USA",
        "annual_allowance_days": "10.00",
        "max_consecutive_days": "10.00",
        "min_notice_days": 0,
        "carryover_days": "0.00",
    },
    {
        "leave_type": "Casual Leave",
        "country": "UK",
        "annual_allowance_days": "8.00",
        "max_consecutive_days": "3.00",
        "min_notice_days": 1,
        "carryover_days": "0.00",
    },
    {
        "leave_type": "Maternity Leave",
        "country": "Canada",
        "annual_allowance_days": "105.00",
        "max_consecutive_days": "105.00",
        "min_notice_days": 30,
        "carryover_days": "0.00",
    },
    {
        "leave_type": "Paternity Leave",
        "country": "Australia",
        "annual_allowance_days": "10.00",
        "max_consecutive_days": "10.00",
        "min_notice_days": 14,
        "carryover_days": "0.00",
    },
    {
        "leave_type": "Bereavement Leave",
        "country": "India",
        "annual_allowance_days": "5.00",
        "max_consecutive_days": "5.00",
        "min_notice_days": 0,
        "carryover_days": "0.00",
    },
)

EXPENSE_POLICY_BASE_LIMITS = {
    "Travel": ("5000.00", "75.00"),
    "Accommodation": ("4000.00", "50.00"),
    "Meals": ("1500.00", "25.00"),
    "Transportation": ("2000.00", "40.00"),
    "Office Supplies": ("800.00", "25.00"),
    "Software": ("10000.00", "100.00"),
    "Training": ("6000.00", "50.00"),
}

COUNTRY_COST_MULTIPLIER = {
    "India": "0.60",
    "USA": "1.20",
    "UK": "1.00",
    "Canada": "1.00",
    "Australia": "1.10",
}

EXPENSE_APPROVAL_BANDS = (
    ("0.00", "500.00", 1, 1),
    ("500.01", "2000.00", 2, 2),
    ("2000.01", "10000.00", 3, 3),
    ("10000.01", None, 4, 4),
)

LEAVE_APPROVAL_BANDS = (
    ("0.00", "3.00", 1, 1),
    ("3.01", "10.00", 2, 2),
    ("10.01", None, 4, 3),
)

# Documented 2026 Country Public Holidays
COUNTRY_HOLIDAYS_2026 = {
    "India": {
        date(2026, 1, 26), date(2026, 3, 3), date(2026, 3, 20),
        date(2026, 4, 14), date(2026, 5, 1), date(2026, 8, 15),
        date(2026, 10, 2), date(2026, 10, 20), date(2026, 11, 8),
        date(2026, 12, 25),
    },
    "USA": {
        date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16),
        date(2026, 5, 25), date(2026, 6, 19), date(2026, 7, 3),
        date(2026, 9, 7), date(2026, 10, 12), date(2026, 11, 11),
        date(2026, 11, 26), date(2026, 12, 25),
    },
    "UK": {
        date(2026, 1, 1), date(2026, 4, 3), date(2026, 4, 6),
        date(2026, 5, 4), date(2026, 5, 25), date(2026, 8, 31),
        date(2026, 12, 25), date(2026, 12, 28),
    },
    "Canada": {
        date(2026, 1, 1), date(2026, 2, 16), date(2026, 4, 3),
        date(2026, 5, 18), date(2026, 7, 1), date(2026, 8, 3),
        date(2026, 9, 7), date(2026, 9, 30), date(2026, 10, 12),
        date(2026, 11, 11), date(2026, 12, 25), date(2026, 12, 26),
    },
    "Australia": {
        date(2026, 1, 1), date(2026, 1, 26), date(2026, 4, 3),
        date(2026, 4, 6), date(2026, 4, 25), date(2026, 6, 8),
        date(2026, 10, 5), date(2026, 12, 25), date(2026, 12, 28),
    },
}

EXPENSE_DESCRIPTIONS = {
    "Travel": (
        "Flight tickets for client meeting",
        "Train tickets for regional workshop",
        "Airport transfer and baggage fee",
        "Inter-city travel for business conference",
        "Travel visa processing fee",
    ),
    "Accommodation": (
        "Hotel stay 2 nights - client site visit",
        "Hotel booking for industry summit",
        "Extended stay lodging during project rollout",
        "Overnight hotel accommodation",
    ),
    "Meals": (
        "Client dinner meeting",
        "Team project working lunch",
        "Lunch during quarterly review session",
        "Business travel dinner allowance",
        "Breakfast catering for team workshop",
    ),
    "Transportation": (
        "Taxi to client headquarters",
        "Rideshare between meeting venues",
        "Car rental for customer site visits",
        "Parking fee at convention center",
        "Public transit pass during business travel",
    ),
    "Office Supplies": (
        "Ergonomic keyboard and wireless mouse",
        "Meeting notebooks and whiteboard accessories",
        "Printer supplies and heavy-duty folders",
        "Desk monitor mount and cable organizers",
        "Presentation materials and document printing",
    ),
    "Software": (
        "Annual developer tool license renewal",
        "Cloud team collaboration monthly subscription",
        "Design and prototyping software license",
        "Security token and SSL certificate renewal",
        "Data analytics platform seat subscription",
    ),
    "Training": (
        "Professional certification examination fee",
        "Technical conference registration pass",
        "Online advanced skills course subscription",
        "Leadership development seminar registration",
        "Compliance training program enrollment",
    ),
}
