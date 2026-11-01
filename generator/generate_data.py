"""Master and transaction data generation entry point for SmartFlow."""

from __future__ import annotations

import argparse
import hashlib
import os
import random
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

from faker import Faker

from generator.constants import (
    AUTHORITY_LEVELS,
    COST_CENTER_BLUEPRINTS,
    COST_CENTER_COUNT,
    COUNTRIES,
    COUNTRY_COST_MULTIPLIER,
    COUNTRY_CURRENCIES,
    COUNTRY_HOLIDAYS_2026,
    COUNTRY_LOCALES,
    DEPARTMENTS,
    EMPLOYEE_COUNT,
    EXPENSE_APPROVAL_BANDS,
    EXPENSE_CATEGORIES,
    EXPENSE_DESCRIPTIONS,
    EXPENSE_POLICY_BASE_LIMITS,
    LEAVE_APPROVAL_BANDS,
    LEAVE_POLICY_COUNT,
    LEAVE_POLICY_TEMPLATES,
    LEAVE_TYPES,
    RANDOM_SEED,
    REFERENCE_DATE,
    REFERENCE_YEAR,
    SCALES,
    STARTER_EMPLOYEE_COUNT,
    TRANSACTION_SCALES,
)
from generator.id_generator import (
    format_approval_rule_id,
    format_cost_center_id,
    format_employee_id,
    format_expense_claim_id,
    format_expense_line_id,
    format_expense_policy_id,
    format_leave_balance_id,
    format_leave_policy_id,
    format_leave_request_id,
)

TWO_PLACES = Decimal("0.01")
REFERENCE_AS_OF = date(*REFERENCE_DATE)
YEAR_START = date(REFERENCE_YEAR, 1, 1)
YEAR_END = date(REFERENCE_YEAR, 12, 31)

IC_TITLES = {
    "Engineering": ("Software Engineer", "QA Engineer", "DevOps Engineer"),
    "Finance": ("Financial Analyst", "Accountant", "Payroll Specialist"),
    "Human Resources": ("HR Generalist", "Recruiter", "People Operations Associate"),
    "Sales": ("Account Executive", "Sales Associate", "Business Development Representative"),
    "Marketing": ("Marketing Specialist", "Content Strategist", "Brand Coordinator"),
    "Operations": ("Operations Analyst", "Logistics Coordinator", "Process Specialist"),
    "Legal": ("Legal Counsel", "Compliance Analyst", "Contract Specialist"),
    "Product": ("Product Analyst", "UX Designer", "Product Operations Associate"),
    "Customer Support": ("Support Specialist", "Customer Success Associate", "Helpdesk Analyst"),
    "Administration": ("Office Coordinator", "Executive Assistant", "Admin Specialist"),
}

MANAGER_TITLES = {
    "Engineering": "Engineering Manager",
    "Finance": "Finance Manager",
    "Human Resources": "HR Manager",
    "Sales": "Sales Manager",
    "Marketing": "Marketing Manager",
    "Operations": "Operations Manager",
    "Legal": "Legal Manager",
    "Product": "Product Manager",
    "Customer Support": "Support Manager",
    "Administration": "Administration Manager",
}

DIRECTOR_TITLES = {
    department: f"Director of {department}" for department in DEPARTMENTS
}

EXECUTIVE_TITLES = {
    "Engineering": "VP Engineering",
    "Finance": "VP Finance",
    "Human Resources": "VP People",
    "Sales": "VP Sales",
    "Marketing": "VP Marketing",
    "Operations": "VP Operations",
    "Legal": "General Counsel",
    "Product": "VP Product",
    "Customer Support": "VP Customer Experience",
    "Administration": "VP Administration",
}


def money(value):
    return Decimal(str(value)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def seed_generators(seed=RANDOM_SEED):
    random.seed(seed)
    faker = Faker()
    faker.seed_instance(seed)
    locale_fakers = {}
    for country, locale in COUNTRY_LOCALES.items():
        country_faker = Faker(locale)
        country_faker.seed_instance(seed)
        locale_fakers[country] = country_faker
    return faker, locale_fakers


def slug_email(first_name, last_name, sequence):
    token = f"{first_name}.{last_name}.{sequence:06d}".lower()
    token = re.sub(r"[^a-z0-9.]+", "", token)
    token = re.sub(r"\.+", ".", token).strip(".")
    return f"{token}@smartflow.example"


def choose_role_counts(employee_count):
    if employee_count <= STARTER_EMPLOYEE_COUNT:
        executives = 4
        directors = 0
        managers = 8
    else:
        executives = 8
        directors = 20
        managers = 50
    individual_contributors = employee_count - 1 - executives - directors - managers
    if individual_contributors < 1:
        raise ValueError("Employee count is too small for the planned hierarchy.")
    return {
        "ceo": 1,
        "executive": executives,
        "director": directors,
        "manager": managers,
        "ic": individual_contributors,
    }


def build_cost_centers():
    centers = []
    for index, (country, department, code, budget) in enumerate(COST_CENTER_BLUEPRINTS, start=1):
        centers.append(
            {
                "cost_center_id": format_cost_center_id(index),
                "code": code,
                "name": f"{country} {department}",
                "country": country,
                "annual_budget": money(budget),
                "owner_employee_id": None,
                "is_active": True,
            }
        )
    if len(centers) != COST_CENTER_COUNT:
        raise ValueError("Cost center blueprint count must be 20.")
    return centers


def assign_levels(employee_count):
    counts = choose_role_counts(employee_count)
    levels = ["ceo"]
    levels.extend(["executive"] * counts["executive"])
    levels.extend(["director"] * counts["director"])
    levels.extend(["manager"] * counts["manager"])
    levels.extend(["ic"] * counts["ic"])
    return levels


def pick_manager(level, employee_id, employees_by_level):
    if level == "ceo":
        return None
    parent_level = {
        "executive": "ceo",
        "director": "executive",
        "manager": "director" if employees_by_level["director"] else "executive",
        "ic": "manager" if employees_by_level["manager"] else "executive",
    }[level]
    candidates = employees_by_level[parent_level]
    manager = candidates[(int(employee_id[-6:]) - 1) % len(candidates)]
    if manager == employee_id:
        raise ValueError(f"Self-management generated for {employee_id}.")
    return manager


def employment_dates(sequence, is_active):
    hire_offset = 200 + (sequence * 17) % 3600
    hire_date = REFERENCE_AS_OF - timedelta(days=hire_offset)
    if hire_date < date(2014, 1, 1):
        hire_date = date(2014, 1, 1) + timedelta(days=sequence % 365)
    termination_date = None
    if not is_active:
        last_day = min(REFERENCE_AS_OF, hire_date + timedelta(days=400 + (sequence % 200)))
        if last_day <= hire_date:
            last_day = hire_date + timedelta(days=30)
        termination_date = last_day
    return hire_date, termination_date


def build_employees(employee_count, cost_centers, locale_fakers):
    levels = assign_levels(employee_count)
    employees = []
    employees_by_level = defaultdict(list)
    emails = set()

    for sequence in range(1, employee_count + 1):
        employee_id = format_employee_id(sequence)
        level = levels[sequence - 1]
        country = COUNTRIES[(sequence - 1) % len(COUNTRIES)]
        if level == "ceo":
            country = "USA"
            department = "Administration"
            job_title = "Chief Executive Officer"
        else:
            department = DEPARTMENTS[(sequence - 1) % len(DEPARTMENTS)]
            if level == "executive":
                job_title = EXECUTIVE_TITLES[department]
            elif level == "director":
                job_title = DIRECTOR_TITLES[department]
            elif level == "manager":
                job_title = MANAGER_TITLES[department]
            else:
                titles = IC_TITLES[department]
                job_title = titles[(sequence - 1) % len(titles)]

        faker = locale_fakers[country]
        first_name = faker.first_name()
        last_name = faker.last_name()
        email = slug_email(first_name, last_name, sequence)
        suffix = 1
        while email in emails:
            email = slug_email(first_name, last_name, sequence + suffix * 100000)
            suffix += 1
        emails.add(email)

        country_centers = [cc for cc in cost_centers if cc["country"] == country]
        cost_center = country_centers[(sequence - 1) % len(country_centers)]
        is_active = sequence % 47 != 0
        hire_date, termination_date = employment_dates(sequence, is_active)
        manager_id = pick_manager(level, employee_id, employees_by_level)

        record = {
            "employee_id": employee_id,
            "first_name": first_name,
            "last_name": last_name,
            "email": email,
            "country": country,
            "department": department,
            "job_title": job_title,
            "manager_id": manager_id,
            "cost_center_id": cost_center["cost_center_id"],
            "hire_date": hire_date,
            "termination_date": termination_date,
            "is_active": is_active,
            "_level": level,
        }
        employees.append(record)
        employees_by_level[level].append(employee_id)

    return employees


def assign_cost_center_owners(cost_centers, employees):
    owners_by_country = defaultdict(list)
    for employee in employees:
        if employee["_level"] in {"executive", "director", "manager"}:
            owners_by_country[employee["country"]].append(employee["employee_id"])
    for index, center in enumerate(cost_centers):
        candidates = owners_by_country[center["country"]] or [
            employee["employee_id"]
            for employee in employees
            if employee["country"] == center["country"]
        ]
        center["owner_employee_id"] = candidates[index % len(candidates)]


def build_leave_policies():
    policies = []
    for index, template in enumerate(LEAVE_POLICY_TEMPLATES, start=1):
        policies.append(
            {
                "leave_policy_id": format_leave_policy_id(index),
                "name": f"{template['country']} {template['leave_type']}",
                "leave_type": template["leave_type"],
                "country": template["country"],
                "annual_allowance_days": money(template["annual_allowance_days"]),
                "max_consecutive_days": money(template["max_consecutive_days"]),
                "min_notice_days": template["min_notice_days"],
                "carryover_days": money(template["carryover_days"]),
                "is_active": True,
            }
        )
    if len(policies) != LEAVE_POLICY_COUNT:
        raise ValueError("Exactly 6 leave policies are required.")
    return policies


def months_employed_in_year(hire_date, termination_date):
    start = max(hire_date, YEAR_START)
    end = termination_date if termination_date else REFERENCE_AS_OF
    end = min(end, REFERENCE_AS_OF)
    if end < start:
        return 0
    months = (end.year - start.year) * 12 + (end.month - start.month) + 1
    return max(0, min(12, months))


def eligible_policies_for_employee(employee, policies, match_country=False):
    selected = []
    for policy in policies:
        if match_country and policy["country"] != employee["country"]:
            continue
        leave_type = policy["leave_type"]
        if leave_type in {"Annual Leave", "Sick Leave", "Bereavement Leave"}:
            selected.append(policy)
        elif leave_type == "Casual Leave":
            selected.append(policy)
        elif leave_type == "Maternity Leave" and int(employee["employee_id"][-6:]) % 2 == 0:
            selected.append(policy)
        elif leave_type == "Paternity Leave" and int(employee["employee_id"][-6:]) % 2 == 1:
            selected.append(policy)
    return selected


def employed_during_reference_year(employee):
    if employee["hire_date"] > REFERENCE_AS_OF:
        return False
    if employee["termination_date"] and employee["termination_date"] < YEAR_START:
        return False
    return True


def build_leave_balances(employees, policies):
    balances = []
    sequence = 1
    seen = set()
    for employee in employees:
        if not employed_during_reference_year(employee):
            continue
        months = months_employed_in_year(employee["hire_date"], employee["termination_date"])
        for policy in eligible_policies_for_employee(employee, policies):
            key = (employee["employee_id"], policy["leave_policy_id"], REFERENCE_YEAR)
            if key in seen:
                continue
            seen.add(key)
            allowance = policy["annual_allowance_days"]
            carryover = policy["carryover_days"]
            opening = min(carryover, allowance)
            if employee["hire_date"].year == REFERENCE_YEAR:
                opening = money("0.00")
            accrued = money(allowance * Decimal(months) / Decimal(12))
            available = opening + accrued
            used_ratio = Decimal((int(employee["employee_id"][-6:]) % 4)) / Decimal(10)
            if policy["leave_type"] in {"Maternity Leave", "Paternity Leave", "Bereavement Leave"}:
                used_ratio = Decimal("0.00")
            used = money(available * used_ratio)
            remaining = money(opening + accrued - used)
            if remaining < 0:
                raise ValueError("Negative remaining leave generated during master data phase.")
            balances.append(
                {
                    "leave_balance_id": format_leave_balance_id(sequence),
                    "employee_id": employee["employee_id"],
                    "leave_policy_id": policy["leave_policy_id"],
                    "year": REFERENCE_YEAR,
                    "opening_balance": opening,
                    "accrued_days": accrued,
                    "used_days": used,
                    "remaining_days": remaining,
                    "as_of_date": REFERENCE_AS_OF,
                }
            )
            sequence += 1
    return balances


def build_expense_policies():
    policies = []
    sequence = 1
    for country in COUNTRIES:
        multiplier = Decimal(COUNTRY_COST_MULTIPLIER[country])
        for category in EXPENSE_CATEGORIES:
            max_amount, receipt = EXPENSE_POLICY_BASE_LIMITS[category]
            policies.append(
                {
                    "expense_policy_id": format_expense_policy_id(sequence),
                    "name": f"{country} {category} Policy",
                    "category": category,
                    "country": country,
                    "max_amount": money(Decimal(max_amount) * multiplier),
                    "receipt_required_above": money(Decimal(receipt) * multiplier),
                    "is_active": True,
                }
            )
            sequence += 1
    return policies


def build_approval_rules():
    rules = []
    sequence = 1
    for country in COUNTRIES:
        for min_amount, max_amount, authority_level, step_order in LEAVE_APPROVAL_BANDS:
            rules.append(
                {
                    "approval_rule_id": format_approval_rule_id(sequence),
                    "request_type": "leave",
                    "country": country,
                    "category": None,
                    "min_amount": money(min_amount),
                    "max_amount": money(max_amount) if max_amount is not None else None,
                    "authority_level": authority_level,
                    "step_order": step_order,
                    "is_active": True,
                }
            )
            sequence += 1
        for category in EXPENSE_CATEGORIES:
            for min_amount, max_amount, authority_level, step_order in EXPENSE_APPROVAL_BANDS:
                rules.append(
                    {
                        "approval_rule_id": format_approval_rule_id(sequence),
                        "request_type": "expense",
                        "country": country,
                        "category": category,
                        "min_amount": money(min_amount),
                        "max_amount": money(max_amount) if max_amount is not None else None,
                        "authority_level": authority_level,
                        "step_order": step_order,
                        "is_active": True,
                    }
                )
                sequence += 1
    return rules


# =====================================================================
# Transaction Data Helpers & Generators (Phase 4)
# =====================================================================

def calculate_business_days(start_date: date, end_date: date, country: str) -> Decimal:
    """Calculate working days between start_date and end_date (inclusive), excluding weekends and holidays."""
    holidays = COUNTRY_HOLIDAYS_2026.get(country, set())
    curr = start_date
    working = 0
    while curr <= end_date:
        if curr.weekday() < 5 and curr not in holidays:
            working += 1
        curr += timedelta(days=1)
    return Decimal(working).quantize(TWO_PLACES)


def advance_business_days(start_date: date, target_days: int, country: str) -> tuple[date, date, Decimal]:
    """Advance from start_date until target_days business days are accumulated."""
    holidays = COUNTRY_HOLIDAYS_2026.get(country, set())
    curr = start_date
    while curr.weekday() >= 5 or curr in holidays:
        curr += timedelta(days=1)

    first_day = curr
    accumulated = 0
    last_day = curr
    while True:
        if curr.weekday() < 5 and curr not in holidays:
            accumulated += 1
            last_day = curr
            if accumulated == target_days:
                return first_day, last_day, Decimal(accumulated).quantize(TWO_PLACES)
        curr += timedelta(days=1)


def build_leave_requests(
    employees,
    leave_policies,
    leave_balances,
    request_count,
    seed=RANDOM_SEED,
    match_employee_country=True,
):
    policies_by_id = {p["leave_policy_id"]: p for p in leave_policies}
    balances_by_emp_pol = {
        (b["employee_id"], b["leave_policy_id"]): Decimal(str(b["remaining_days"]))
        for b in leave_balances
    }

    remaining_balance = dict(balances_by_emp_pol)
    approved_ranges = defaultdict(list)

    if match_employee_country:
        eligible_employees = [
            e for e in employees
            if employed_during_reference_year(e) and any(
                (e["employee_id"], pid) in remaining_balance and policies_by_id[pid]["country"] == e["country"]
                for pid in policies_by_id
            )
        ]
    else:
        eligible_employees = [
            e for e in employees
            if employed_during_reference_year(e) and any(
                (e["employee_id"], pid) in remaining_balance for pid in policies_by_id
            )
        ]

    requests = []

    for seq in range(1, request_count + 1):
        req_id = format_leave_request_id(REFERENCE_YEAR, seq)
        emp_idx = (seq - 1) % len(eligible_employees)
        employee = eligible_employees[emp_idx]
        emp_id = employee["employee_id"]
        country = employee["country"]

        if match_employee_country:
            emp_pols = [
                policies_by_id[pid] for (eid, pid), bal in remaining_balance.items()
                if eid == emp_id and bal >= Decimal("1.00") and policies_by_id[pid]["country"] == country
            ]
            if not emp_pols:
                emp_pols = [
                    policies_by_id[pid] for (eid, pid) in balances_by_emp_pol
                    if eid == emp_id and policies_by_id[pid]["country"] == country
                ]
        else:
            emp_pols = [
                policies_by_id[pid] for (eid, pid), bal in remaining_balance.items()
                if eid == emp_id and bal >= Decimal("1.00")
            ]
            if not emp_pols:
                emp_pols = [
                    policies_by_id[pid] for (eid, pid) in balances_by_emp_pol
                    if eid == emp_id
                ]

        policy = emp_pols[(seq * 3) % len(emp_pols)]
        pol_id = policy["leave_policy_id"]
        avail = remaining_balance.get((emp_id, pol_id), Decimal("0.00"))

        status_bucket = seq % 10
        if status_bucket == 0:
            status = "rejected"
        elif status_bucket == 1:
            status = "submitted"
        else:
            status = "approved"

        max_consec = int(Decimal(str(policy.get("max_consecutive_days") or 5)))
        max_possible = min(int(avail) if status == "approved" else 5, max_consec, 5)
        if max_possible < 1:
            max_possible = 1

        requested_days = 1 + (seq % max_possible)

        min_start = max(employee["hire_date"], date(2026, 1, 5))
        max_end = employee["termination_date"] if employee["termination_date"] else date(2026, 12, 15)
        span_days = (max_end - min_start).days
        if span_days < 10:
            span_days = 10
            min_start = date(2026, 1, 5)

        start_offset = (seq * 47) % max(1, span_days - 10)
        candidate_start = min_start + timedelta(days=start_offset)

        first_day = candidate_start
        last_day = candidate_start
        bdays = Decimal("1.00")

        for attempt in range(35):
            first_day, last_day, bdays = advance_business_days(candidate_start, requested_days, country)
            if last_day > max_end:
                candidate_start = min_start + timedelta(days=(attempt * 7) % max(1, span_days - 10))
                continue

            has_overlap = False
            if status == "approved":
                for existing_start, existing_end in approved_ranges[emp_id]:
                    if not (last_day < existing_start or first_day > existing_end):
                        has_overlap = True
                        break

            if not has_overlap:
                break
            candidate_start += timedelta(days=7)

        if status == "approved":
            approved_ranges[emp_id].append((first_day, last_day))
            remaining_balance[(emp_id, pol_id)] = max(Decimal("0.00"), avail - bdays)

        notice = max(int(policy.get("min_notice_days") or 0), 1)
        sub_date = first_day - timedelta(days=notice + (seq % 5))
        if sub_date < employee["hire_date"]:
            sub_date = employee["hire_date"]

        sub_time = datetime(sub_date.year, sub_date.month, sub_date.day, 9, 30, (seq * 13) % 60, tzinfo=timezone.utc)

        if status in {"approved", "rejected"}:
            dec_date = min(sub_date + timedelta(days=1 + (seq % 3)), first_day)
            dec_time = datetime(dec_date.year, dec_date.month, dec_date.day, 15, 0, (seq * 17) % 60, tzinfo=timezone.utc)
        else:
            dec_time = None

        record = {
            "leave_request_id": req_id,
            "employee_id": emp_id,
            "leave_policy_id": pol_id,
            "start_date": first_day,
            "end_date": last_day,
            "business_days": bdays,
            "status": status,
            "submitted_at": sub_time,
            "decided_at": dec_time,
            "created_at": sub_time,
            "updated_at": dec_time if dec_time else sub_time,
            "_issue_type": None,
        }
        requests.append(record)

    return requests


def build_expense_claims_and_lines(employees, expense_policies, cost_centers, claim_count, seed=RANDOM_SEED):
    policies_by_country_cat = {
        (p["country"], p["category"]): p for p in expense_policies
    }

    eligible_employees = [e for e in employees if employed_during_reference_year(e)]

    claims = []
    lines = []

    for seq in range(1, claim_count + 1):
        claim_id = format_expense_claim_id(REFERENCE_YEAR, seq)
        emp_idx = (seq - 1) % len(eligible_employees)
        employee = eligible_employees[emp_idx]
        emp_id = employee["employee_id"]
        country = employee["country"]
        currency = COUNTRY_CURRENCIES.get(country, "USD")
        cost_center_id = employee["cost_center_id"]

        category = EXPENSE_CATEGORIES[(seq - 1) % len(EXPENSE_CATEGORIES)]
        policy = policies_by_country_cat[(country, category)]
        policy_id = policy["expense_policy_id"]
        policy_max = Decimal(str(policy["max_amount"]))
        receipt_threshold = Decimal(str(policy["receipt_required_above"]))

        min_date = max(employee["hire_date"], date(2026, 1, 15))
        max_date = min(employee["termination_date"] or REFERENCE_AS_OF, REFERENCE_AS_OF)
        if max_date < min_date:
            min_date = employee["hire_date"]
            max_date = min_date + timedelta(days=30)

        span = max(1, (max_date - min_date).days)
        claim_date = min_date + timedelta(days=(seq * 29) % span)

        status_bucket = seq % 10
        if status_bucket == 0:
            status = "rejected"
        elif status_bucket == 1:
            status = "submitted"
        else:
            status = "approved"

        sub_time = datetime(claim_date.year, claim_date.month, claim_date.day, 10, 15, (seq * 11) % 60, tzinfo=timezone.utc)
        if status in {"approved", "rejected"}:
            dec_date = min(claim_date + timedelta(days=1 + (seq % 4)), REFERENCE_AS_OF)
            dec_time = datetime(dec_date.year, dec_date.month, dec_date.day, 16, 45, (seq * 19) % 60, tzinfo=timezone.utc)
        else:
            dec_time = None

        num_lines = 1 + ((seq * 7) % 10)

        target_fraction = Decimal("0.15") + Decimal((seq * 13) % 65) / Decimal("100")
        target_total = money(policy_max * target_fraction)

        claim_lines = []
        allocated = Decimal("0.00")
        base_share = money(target_total / Decimal(num_lines))
        if base_share < Decimal("5.00"):
            base_share = Decimal("5.00")

        for line_num in range(1, num_lines + 1):
            line_id = format_expense_line_id(claim_id, line_num)
            line_date = claim_date - timedelta(days=(line_num * 2) % 10)
            if line_date < employee["hire_date"]:
                line_date = employee["hire_date"]

            if line_num < num_lines:
                adj = Decimal(((seq + line_num) % 5) - 2) * Decimal("4.00")
                line_amt = max(Decimal("5.00"), base_share + adj)
                if allocated + line_amt >= target_total:
                    line_amt = max(Decimal("1.00"), (target_total - allocated) / Decimal(num_lines - line_num + 1))
                line_amt = money(line_amt)
                allocated += line_amt
            else:
                line_amt = money(target_total - allocated)
                if line_amt <= Decimal("0.00"):
                    line_amt = Decimal("10.00")
                allocated += line_amt

            descs = EXPENSE_DESCRIPTIONS[category]
            base_desc = descs[(seq + line_num) % len(descs)]

            if line_amt >= receipt_threshold:
                rec_filename = f"receipt_{claim_id}_{line_num:02d}.pdf"
                rec_hash = hashlib.sha256(f"{claim_id}:{line_num}:{line_amt}:{line_date}:{seed}".encode()).hexdigest()
                full_desc = f"{base_desc} [Receipt: {rec_filename} | SHA256: {rec_hash}]"
            else:
                rec_filename = None
                rec_hash = None
                full_desc = base_desc

            line_record = {
                "expense_line_id": line_id,
                "expense_claim_id": claim_id,
                "line_number": line_num,
                "category": category,
                "description": full_desc,
                "expense_date": line_date,
                "amount": line_amt,
                "country": country,
                "created_at": sub_time,
                "updated_at": dec_time if dec_time else sub_time,
                "_receipt_filename": rec_filename,
                "_receipt_hash": rec_hash,
            }
            claim_lines.append(line_record)
            lines.append(line_record)

        final_total = money(sum(l["amount"] for l in claim_lines))

        claim_record = {
            "expense_claim_id": claim_id,
            "employee_id": emp_id,
            "cost_center_id": cost_center_id,
            "expense_policy_id": policy_id,
            "claim_date": claim_date,
            "country": country,
            "currency": currency,
            "total_amount": final_total,
            "status": status,
            "submitted_at": sub_time,
            "decided_at": dec_time,
            "created_at": sub_time,
            "updated_at": dec_time if dec_time else sub_time,
        }
        claims.append(claim_record)

    return claims, lines


def generate_transaction_data(master_payload, scale="full", seed=RANDOM_SEED):
    if scale not in TRANSACTION_SCALES:
        raise ValueError("Scale must be 'starter' or 'full'.")
    req_count, claim_count = TRANSACTION_SCALES[scale]

    leave_requests = build_leave_requests(
        master_payload["employees"],
        master_payload["leave_policies"],
        master_payload["leave_balances"],
        req_count,
        seed=seed,
    )
    expense_claims, expense_lines = build_expense_claims_and_lines(
        master_payload["employees"],
        master_payload["expense_policies"],
        master_payload["cost_centers"],
        claim_count,
        seed=seed,
    )
    return {
        "leave_requests": leave_requests,
        "expense_claims": expense_claims,
        "expense_lines": expense_lines,
    }


def strip_private_fields(records):
    cleaned = []
    for record in records:
        cleaned.append({key: value for key, value in record.items() if not key.startswith("_")})
    return cleaned


def manager_map(employees):
    return {employee["employee_id"]: employee["manager_id"] for employee in employees}


def has_manager_cycle(employees):
    links = manager_map(employees)
    state = {}

    def dfs(node, stack):
        if not node:
            return False
        if node in stack:
            return True
        if state.get(node) == "done":
            return False
        stack.add(node)
        if dfs(links.get(node), stack):
            return True
        stack.remove(node)
        state[node] = "done"
        return False

    return any(dfs(employee_id, set()) for employee_id in links)


def existing_master_counts(session, models):
    return {
        "cost_centers": session.query(models["CostCenter"]).count(),
        "employees": session.query(models["Employee"]).count(),
        "leave_policies": session.query(models["LeavePolicy"]).count(),
        "leave_balances": session.query(models["LeaveBalance"]).count(),
        "expense_policies": session.query(models["ExpensePolicy"]).count(),
        "approval_rules": session.query(models["ApprovalRule"]).count(),
    }


def existing_transaction_counts(session, models):
    from models import ApprovalStep, Reimbursement, WorkflowEvent
    return {
        "leave_requests": session.query(models["LeaveRequest"]).count(),
        "expense_claims": session.query(models["ExpenseClaim"]).count(),
        "expense_lines": session.query(models["ExpenseLine"]).count(),
        "approval_steps": session.query(ApprovalStep).count(),
        "reimbursements": session.query(Reimbursement).count(),
        "workflow_events": session.query(WorkflowEvent).count(),
    }


def reset_master_data(session, models):
    from models import ApprovalStep, Reimbursement, WorkflowEvent
    session.query(WorkflowEvent).delete()
    session.query(Reimbursement).delete()
    session.query(ApprovalStep).delete()
    session.query(models["CostCenter"]).update({models["CostCenter"].owner_employee_id: None})
    session.flush()
    session.query(models["LeaveBalance"]).delete()
    session.query(models["Employee"]).update({models["Employee"].manager_id: None})
    session.flush()
    session.query(models["Employee"]).delete()
    session.query(models["CostCenter"]).delete()
    session.query(models["LeavePolicy"]).delete()
    session.query(models["ExpensePolicy"]).delete()
    session.query(models["ApprovalRule"]).delete()
    session.flush()


def reset_transaction_data(session, models):
    from models import ApprovalStep, Reimbursement, WorkflowEvent
    session.query(WorkflowEvent).delete()
    session.query(Reimbursement).delete()
    session.query(ApprovalStep).delete()
    session.query(models["ExpenseLine"]).delete()
    session.query(models["ExpenseClaim"]).delete()
    session.query(models["LeaveRequest"]).delete()
    session.flush()


def persist_master_data(payload, reset=False):
    from app import app, init_database
    from models import (
        ApprovalRule,
        CostCenter,
        Employee,
        ExpensePolicy,
        LeaveBalance,
        LeavePolicy,
        db,
    )

    init_database()
    model_map = {
        "CostCenter": CostCenter,
        "Employee": Employee,
        "LeavePolicy": LeavePolicy,
        "LeaveBalance": LeaveBalance,
        "ExpensePolicy": ExpensePolicy,
        "ApprovalRule": ApprovalRule,
    }

    with app.app_context():
        counts = existing_master_counts(db.session, model_map)
        if any(counts.values()) and not reset:
            return {
                "status": "skipped",
                "reason": "Master data already exists. Re-run with --reset to replace it.",
                "existing": counts,
            }

        try:
            if reset:
                reset_master_data(db.session, model_map)

            initial_centers = [
                {**center, "owner_employee_id": None}
                for center in payload["cost_centers"]
            ]
            db.session.bulk_insert_mappings(CostCenter, initial_centers)
            db.session.flush()
            db.session.bulk_insert_mappings(Employee, strip_private_fields(payload["employees"]))
            db.session.flush()
            for center in payload["cost_centers"]:
                db.session.query(CostCenter).filter_by(
                    cost_center_id=center["cost_center_id"]
                ).update({"owner_employee_id": center["owner_employee_id"]})
            db.session.flush()
            db.session.bulk_insert_mappings(LeavePolicy, payload["leave_policies"])
            db.session.bulk_insert_mappings(ExpensePolicy, payload["expense_policies"])
            db.session.bulk_insert_mappings(ApprovalRule, payload["approval_rules"])
            db.session.bulk_insert_mappings(LeaveBalance, payload["leave_balances"])
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise

        return {"status": "inserted", "existing": existing_master_counts(db.session, model_map)}


def persist_transaction_data(trans_payload, reset=False):
    from app import app, init_database
    from models import (
        ExpenseClaim,
        ExpenseLine,
        LeaveRequest,
        db,
    )

    init_database()
    model_map = {
        "LeaveRequest": LeaveRequest,
        "ExpenseClaim": ExpenseClaim,
        "ExpenseLine": ExpenseLine,
    }

    with app.app_context():
        counts = existing_transaction_counts(db.session, model_map)
        if any(counts.values()) and not reset:
            return {
                "status": "skipped",
                "reason": "Transaction data already exists. Re-run with --reset to replace it.",
                "existing": counts,
            }

        try:
            if reset:
                reset_transaction_data(db.session, model_map)

            db.session.bulk_insert_mappings(LeaveRequest, strip_private_fields(trans_payload["leave_requests"]))
            db.session.flush()
            db.session.bulk_insert_mappings(ExpenseClaim, trans_payload["expense_claims"])
            db.session.flush()
            db.session.bulk_insert_mappings(ExpenseLine, strip_private_fields(trans_payload["expense_lines"]))
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise

        return {"status": "inserted", "existing": existing_transaction_counts(db.session, model_map)}


def validate_approval_ranges(rules):
    grouped = defaultdict(list)
    for rule in rules:
        grouped[(rule["request_type"], rule["country"], rule["category"])].append(rule)
    for key, items in grouped.items():
        ordered = sorted(items, key=lambda item: (item["step_order"], item["min_amount"] or Decimal("0")))
        previous_max = None
        previous_step = None
        for item in ordered:
            if item["min_amount"] is None:
                return f"Missing min_amount for {item['approval_rule_id']}"
            if item["max_amount"] is not None and item["min_amount"] > item["max_amount"]:
                return f"Invalid range for {item['approval_rule_id']}"
            if previous_max is not None and item["min_amount"] <= previous_max:
                return f"Overlapping approval range in {key}"
            if previous_step is not None and item["step_order"] <= previous_step:
                return f"Non-increasing step order in {key}"
            previous_max = item["max_amount"]
            previous_step = item["step_order"]
        if ordered[-1]["max_amount"] is not None:
            return f"Unbounded top band missing for {key}"
    return None


def validate_master_data(payload, expected_employees):
    issues = []
    employees = payload["employees"]
    employee_ids = [employee["employee_id"] for employee in employees]
    emails = [employee["email"] for employee in employees]
    cost_center_ids = {center["cost_center_id"] for center in payload["cost_centers"]}
    policy_ids = {policy["leave_policy_id"] for policy in payload["leave_policies"]}

    if len(employees) != expected_employees:
        issues.append(f"Expected {expected_employees} employees, found {len(employees)}")
    if len(payload["leave_policies"]) != 6:
        issues.append("Expected exactly 6 leave policies")
    if len(set(employee_ids)) != len(employee_ids):
        issues.append("Duplicate employee primary keys")
    if len(set(emails)) != len(emails):
        issues.append("Duplicate employee emails")
    if len(cost_center_ids) != len(payload["cost_centers"]):
        issues.append("Duplicate cost center primary keys")

    for employee in employees:
        if employee["manager_id"] == employee["employee_id"]:
            issues.append(f"Self-management: {employee['employee_id']}")
        if employee["manager_id"] and employee["manager_id"] not in employee_ids:
            issues.append(f"Invalid manager {employee['manager_id']}")
        if employee["cost_center_id"] not in cost_center_ids:
            issues.append(f"Invalid cost center on {employee['employee_id']}")
        if employee["country"] not in COUNTRIES:
            issues.append(f"Invalid country on {employee['employee_id']}")
        if employee["department"] not in DEPARTMENTS:
            issues.append(f"Invalid department on {employee['employee_id']}")

    if has_manager_cycle(employees):
        issues.append("Circular manager relationship detected")

    balance_keys = []
    for balance in payload["leave_balances"]:
        expected = money(
            balance["opening_balance"] + balance["accrued_days"] - balance["used_days"]
        )
        if expected != money(balance["remaining_days"]):
            issues.append(f"Leave arithmetic mismatch {balance['leave_balance_id']}")
        if money(balance["remaining_days"]) < 0:
            issues.append(f"Negative remaining days {balance['leave_balance_id']}")
        if balance["employee_id"] not in employee_ids:
            issues.append(f"Invalid employee on {balance['leave_balance_id']}")
        if balance["leave_policy_id"] not in policy_ids:
            issues.append(f"Invalid leave policy on {balance['leave_balance_id']}")
        balance_keys.append(
            (balance["employee_id"], balance["leave_policy_id"], balance["year"])
        )
    if len(balance_keys) != len(set(balance_keys)):
        issues.append("Duplicate employee-policy-year leave balances")

    for policy in payload["leave_policies"]:
        if policy["leave_type"] not in LEAVE_TYPES:
            issues.append(f"Invalid leave type {policy['leave_type']}")
        if policy["country"] not in COUNTRIES:
            issues.append(f"Invalid leave policy country {policy['country']}")

    for policy in payload["expense_policies"]:
        if policy["category"] not in EXPENSE_CATEGORIES:
            issues.append(f"Invalid expense category {policy['category']}")
        if policy["country"] not in COUNTRIES:
            issues.append(f"Invalid expense policy country {policy['country']}")

    range_error = validate_approval_ranges(payload["approval_rules"])
    if range_error:
        issues.append(range_error)

    for center in payload["cost_centers"]:
        if center["owner_employee_id"] not in employee_ids:
            issues.append(f"Invalid cost center owner {center['owner_employee_id']}")

    return issues


def validate_transaction_data(trans_payload, master_payload, expected_requests, expected_claims):
    issues = []
    leave_requests = trans_payload["leave_requests"]
    expense_claims = trans_payload["expense_claims"]
    expense_lines = trans_payload["expense_lines"]

    emp_map = {e["employee_id"]: e for e in master_payload["employees"]}
    lpol_map = {p["leave_policy_id"]: p for p in master_payload["leave_policies"]}
    epol_map = {p["expense_policy_id"]: p for p in master_payload["expense_policies"]}
    cc_map = {c["cost_center_id"]: c for c in master_payload["cost_centers"]}

    # 1. Leave Requests Validation
    if len(leave_requests) != expected_requests:
        issues.append(f"Expected {expected_requests} leave requests, found {len(leave_requests)}")

    req_ids = [r["leave_request_id"] for r in leave_requests]
    if len(req_ids) != len(set(req_ids)):
        issues.append("Duplicate leave_request_id found")

    approved_by_emp = defaultdict(list)

    for r in leave_requests:
        rid = r["leave_request_id"]
        eid = r["employee_id"]
        pid = r["leave_policy_id"]

        if eid not in emp_map:
            issues.append(f"Invalid employee_id {eid} in {rid}")
            continue
        emp = emp_map[eid]

        if pid not in lpol_map:
            issues.append(f"Invalid leave_policy_id {pid} in {rid}")
            continue

        if r["start_date"] > r["end_date"]:
            issues.append(f"start_date > end_date in {rid}")

        if r["start_date"] < emp["hire_date"]:
            issues.append(f"start_date before hire_date in {rid}")
        if emp["termination_date"] and r["end_date"] > emp["termination_date"]:
            issues.append(f"end_date after termination_date in {rid}")

        expected_bdays = calculate_business_days(r["start_date"], r["end_date"], emp["country"])
        if money(r["business_days"]) != expected_bdays:
            issues.append(f"Business days mismatch in {rid}: calculated {expected_bdays}, recorded {r['business_days']}")

        if r["status"] not in {"submitted", "approved", "rejected"}:
            issues.append(f"Invalid status {r['status']} in {rid}")

        if r["status"] == "approved":
            for ex_start, ex_end in approved_by_emp[eid]:
                if not (r["end_date"] < ex_start or r["start_date"] > ex_end):
                    issues.append(f"Overlapping approved leave for employee {eid} in {rid}")
                    break
            approved_by_emp[eid].append((r["start_date"], r["end_date"]))

    # 2. Expense Claims Validation
    if len(expense_claims) != expected_claims:
        issues.append(f"Expected {expected_claims} expense claims, found {len(expense_claims)}")

    claim_ids = [c["expense_claim_id"] for c in expense_claims]
    if len(claim_ids) != len(set(claim_ids)):
        issues.append("Duplicate expense_claim_id found")

    lines_by_claim = defaultdict(list)
    for line in expense_lines:
        lines_by_claim[line["expense_claim_id"]].append(line)

    rec_hashes = []

    for c in expense_claims:
        cid = c["expense_claim_id"]
        eid = c["employee_id"]
        pid = c["expense_policy_id"]
        ccid = c["cost_center_id"]

        if eid not in emp_map:
            issues.append(f"Invalid employee_id {eid} in {cid}")
            continue
        emp = emp_map[eid]

        if pid not in epol_map:
            issues.append(f"Invalid expense_policy_id {pid} in {cid}")
            continue
        pol = epol_map[pid]

        if ccid not in cc_map:
            issues.append(f"Invalid cost_center_id {ccid} in {cid}")

        if c["country"] != emp["country"]:
            issues.append(f"Claim country mismatch in {cid}: emp {emp['country']}, claim {c['country']}")
        if pol["country"] != c["country"]:
            issues.append(f"Policy country mismatch in {cid}")

        if c["claim_date"] < emp["hire_date"]:
            issues.append(f"claim_date before hire_date in {cid}")
        if emp["termination_date"] and c["claim_date"] > emp["termination_date"]:
            issues.append(f"claim_date after termination_date in {cid}")

        if money(c["total_amount"]) > money(pol["max_amount"]):
            issues.append(f"Claim total {c['total_amount']} exceeds policy limit {pol['max_amount']} in {cid}")

        clines = lines_by_claim.get(cid, [])
        if not (1 <= len(clines) <= 10):
            issues.append(f"Invalid line count {len(clines)} in {cid}")

        line_sum = money(sum(l["amount"] for l in clines))
        if money(c["total_amount"]) != line_sum:
            issues.append(f"Claim total reconciliation failure in {cid}: claim {c['total_amount']} vs lines sum {line_sum}")

        for l in clines:
            if l.get("_receipt_hash"):
                rec_hashes.append(l["_receipt_hash"])

    if len(rec_hashes) != len(set(rec_hashes)):
        issues.append(f"Duplicate receipt hashes found: {len(rec_hashes)} vs {len(set(rec_hashes))} unique")

    return issues


def print_sample(title, records, fields):
    print(f"\n{title}")
    for record in records[:2]:
        sample = {field: record.get(field) for field in fields}
        print(f"  {sample}")


def print_summary(master_payload, trans_payload, master_persist, trans_persist, issues, scale, workflow_stats=None):
    print("=" * 60)
    print("SmartFlow Data Generation Summary (Phase 9)")
    print("=" * 60)
    print(f"Scale: {scale}")
    print(f"Seed: {RANDOM_SEED}")
    print(f"Master persist status: {master_persist['status']}")
    if master_persist["status"] == "skipped":
        print(f"  {master_persist['reason']}")
    print(f"Transaction persist status: {trans_persist['status']}")
    if trans_persist["status"] == "skipped":
        print(f"  {trans_persist['reason']}")

    print("\nMaster Record Counts:")
    print(f"  CostCenter     {len(master_payload['cost_centers'])}")
    print(f"  Employee       {len(master_payload['employees'])}")
    print(f"  LeavePolicy    {len(master_payload['leave_policies'])}")
    print(f"  LeaveBalance   {len(master_payload['leave_balances'])}")
    print(f"  ExpensePolicy  {len(master_payload['expense_policies'])}")
    print(f"  ApprovalRule   {len(master_payload['approval_rules'])}")

    print("\nTransaction Record Counts:")
    print(f"  LeaveRequest   {len(trans_payload['leave_requests'])}")
    print(f"  ExpenseClaim   {len(trans_payload['expense_claims'])}")
    print(f"  ExpenseLine    {len(trans_payload['expense_lines'])}")

    if workflow_stats:
        print("\nWorkflow & Reimbursement Lifecycle Simulation:")
        print(f"  Leave Requests:  {workflow_stats['leave']['total']} total | {workflow_stats['leave']['eligible']} eligible | {workflow_stats['leave']['ineligible']} ineligible")
        print(f"                   {workflow_stats['leave']['approved']} approved | {workflow_stats['leave']['rejected']} rejected | {workflow_stats['leave']['pending']} pending")
        print(f"  Expense Claims:  {workflow_stats['expense']['total']} total | {workflow_stats['expense']['eligible']} eligible | {workflow_stats['expense']['ineligible']} ineligible")
        print(f"                   {workflow_stats['expense']['approved']} approved | {workflow_stats['expense']['rejected']} rejected | {workflow_stats['expense']['pending']} pending")
        print(f"  Approval Steps:  {workflow_stats['steps']['created']} created | {workflow_stats['steps']['approved']} approved | {workflow_stats['steps']['rejected']} rejected | {workflow_stats['steps']['pending']} pending")
        print(f"  Reimbursements:  {workflow_stats['reimbursements']['created']} created | {workflow_stats['reimbursements']['paid']} paid | {workflow_stats['reimbursements']['pending']} pending")
        print(f"                   Total Reimbursed: {workflow_stats['reimbursements']['total_reimbursed_amount']:.2f} | Total Paid: {workflow_stats['reimbursements']['total_paid_amount']:.2f}")
        print(f"  Workflow Events: {workflow_stats['workflow_events']} auditable events logged")

    print_sample(
        "Sample leave requests",
        trans_payload["leave_requests"],
        ["leave_request_id", "employee_id", "leave_policy_id", "start_date", "end_date", "business_days", "status"],
    )
    print_sample(
        "Sample expense claims",
        trans_payload["expense_claims"],
        ["expense_claim_id", "employee_id", "cost_center_id", "claim_date", "currency", "total_amount", "status"],
    )
    print_sample(
        "Sample expense lines",
        trans_payload["expense_lines"],
        ["expense_line_id", "expense_claim_id", "line_number", "category", "description", "amount"],
    )

    if issues:
        print("\nValidation FAILED:")
        for issue in issues[:10]:
            print(f"  - {issue}")
    else:
        print("\nValidation PASSED: all master and transaction checks succeeded.")


def simulate_workflow_lifecycle(
    seed: int = RANDOM_SEED,
    target_approved_ratio: float = 0.80,
    target_rejected_ratio: float = 0.10,
    reimburse_ratio: float = 0.85,
    pay_ratio: float = 0.80,
) -> dict[str, Any]:
    """Deterministically simulate the end-to-end workflow lifecycle on existing requests.

    Requirements:
    1. Evaluates requests using business logic services (LeaveService & ExpenseService).
    2. Routes eligible records through ApprovalService to create sequential pending steps.
    3. Keeps ineligible records out of the workflow, marking them rejected without steps.
    4. Deterministically simulates authorized approver decisions using seed:
       - ~80% approved through all required steps
       - ~10% rejected by an authorized approver
       - ~10% remain pending
    5. Finalized approved ExpenseClaims are reimbursed (~85%), and reimbursements paid (~80%).
    6. Ensures full reproducibility with RANDOM_SEED = 20261022.
    """
    import random
    from app import app
    from models import (
        ApprovalStep,
        ExpenseClaim,
        LeaveRequest,
        Reimbursement,
        WorkflowEvent,
        db,
    )
    from services.reimbursement_service import (
        create_reimbursement_for_approved_claim,
        mark_reimbursement_paid,
    )
    from services.workflow_service import (
        process_step_decision,
        submit_and_route_request,
    )

    rng = random.Random(seed)

    with app.app_context():
        leave_requests = LeaveRequest.query.order_by(LeaveRequest.leave_request_id.asc()).all()
        expense_claims = ExpenseClaim.query.order_by(ExpenseClaim.expense_claim_id.asc()).all()

        stats = {
            "leave": {"total": len(leave_requests), "eligible": 0, "ineligible": 0, "approved": 0, "rejected": 0, "pending": 0},
            "expense": {"total": len(expense_claims), "eligible": 0, "ineligible": 0, "approved": 0, "rejected": 0, "pending": 0},
            "steps": {"created": 0, "approved": 0, "rejected": 0, "pending": 0},
            "reimbursements": {"created": 0, "paid": 0, "pending": 0, "total_reimbursed_amount": Decimal("0.00"), "total_paid_amount": Decimal("0.00")},
            "workflow_events": 0,
        }

        # 1. Process Leave Requests
        for req in leave_requests:
            req.status = "submitted"
            req.decided_at = None
        db.session.commit()

        for req in leave_requests:
            route_res = submit_and_route_request("leave", req.leave_request_id)
            if not route_res.get("can_route"):
                stats["leave"]["ineligible"] += 1
                stats["leave"]["rejected"] += 1
                continue

            stats["leave"]["eligible"] += 1
            steps = (
                ApprovalStep.query.filter_by(leave_request_id=req.leave_request_id)
                .order_by(ApprovalStep.step_order.asc())
                .all()
            )
            stats["steps"]["created"] += len(steps)

            roll = rng.random()
            if roll < target_approved_ratio:
                for s in steps:
                    process_step_decision(s.approval_step_id, s.approver_employee_id, "approved", "Simulated approval")
                    stats["steps"]["approved"] += 1
                stats["leave"]["approved"] += 1
            elif roll < target_approved_ratio + target_rejected_ratio:
                process_step_decision(steps[0].approval_step_id, steps[0].approver_employee_id, "rejected", "Simulated rejection")
                stats["steps"]["rejected"] += 1
                stats["steps"]["pending"] += len(steps) - 1
                stats["leave"]["rejected"] += 1
            else:
                stats["steps"]["pending"] += len(steps)
                stats["leave"]["pending"] += 1

        # 2. Process Expense Claims
        for claim in expense_claims:
            claim.status = "submitted"
            claim.decided_at = None
        db.session.commit()

        for claim in expense_claims:
            route_res = submit_and_route_request("expense", claim.expense_claim_id)
            if not route_res.get("can_route"):
                stats["expense"]["ineligible"] += 1
                stats["expense"]["rejected"] += 1
                continue

            stats["expense"]["eligible"] += 1
            steps = (
                ApprovalStep.query.filter_by(expense_claim_id=claim.expense_claim_id)
                .order_by(ApprovalStep.step_order.asc())
                .all()
            )
            stats["steps"]["created"] += len(steps)

            roll = rng.random()
            if roll < target_approved_ratio:
                for s in steps:
                    process_step_decision(s.approval_step_id, s.approver_employee_id, "approved", "Simulated approval")
                    stats["steps"]["approved"] += 1
                stats["expense"]["approved"] += 1

                # Reimbursement lifecycle
                r_roll = rng.random()
                if r_roll < reimburse_ratio:
                    dec_at = claim.decided_at or datetime.now(timezone.utc)
                    if dec_at.tzinfo is None:
                        dec_at = dec_at.replace(tzinfo=timezone.utc)
                    reimb_created = dec_at + timedelta(hours=4)
                    reimb = create_reimbursement_for_approved_claim(
                        claim.expense_claim_id,
                        created_at=reimb_created,
                    )
                    stats["reimbursements"]["created"] += 1
                    stats["reimbursements"]["total_reimbursed_amount"] += Decimal(str(reimb.amount))

                    p_roll = rng.random()
                    if p_roll < pay_ratio:
                        pay_time = reimb_created + timedelta(days=2)
                        paid = mark_reimbursement_paid(
                            reimb.reimbursement_id,
                            paid_at=pay_time,
                        )
                        stats["reimbursements"]["paid"] += 1
                        stats["reimbursements"]["total_paid_amount"] += Decimal(str(paid.amount))
                    else:
                        stats["reimbursements"]["pending"] += 1
            elif roll < target_approved_ratio + target_rejected_ratio:
                process_step_decision(steps[0].approval_step_id, steps[0].approver_employee_id, "rejected", "Simulated rejection")
                stats["steps"]["rejected"] += 1
                stats["steps"]["pending"] += len(steps) - 1
                stats["expense"]["rejected"] += 1
            else:
                stats["steps"]["pending"] += len(steps)
                stats["expense"]["pending"] += 1

        stats["workflow_events"] = WorkflowEvent.query.count()
        return stats


def generate_master_data(scale="full", seed=RANDOM_SEED):
    if scale not in SCALES:
        raise ValueError("Scale must be 'starter' or 'full'.")
    employee_count = SCALES[scale]
    _faker, locale_fakers = seed_generators(seed)
    cost_centers = build_cost_centers()
    employees = build_employees(employee_count, cost_centers, locale_fakers)
    assign_cost_center_owners(cost_centers, employees)
    leave_policies = build_leave_policies()
    leave_balances = build_leave_balances(employees, leave_policies)
    expense_policies = build_expense_policies()
    approval_rules = build_approval_rules()
    return {
        "cost_centers": cost_centers,
        "employees": employees,
        "leave_policies": leave_policies,
        "leave_balances": leave_balances,
        "expense_policies": expense_policies,
        "approval_rules": approval_rules,
    }


def generate_all(
    seed=RANDOM_SEED,
    scale="full",
    reset=False,
    simulate_workflow=False,
    target_db=None,
):
    if target_db:
        from pathlib import Path
        from sqlalchemy import create_engine
        db_path = Path(target_db).resolve().as_posix()
        os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
        from app import app
        from models import db
        app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"
        db._app_engines[app] = {None: create_engine(f"sqlite:///{db_path}")}
        with app.app_context():
            db.create_all()

    master_payload = generate_master_data(scale=scale, seed=seed)
    master_persist_result = persist_master_data(master_payload, reset=reset)
    master_issues = validate_master_data(master_payload, SCALES[scale])

    trans_payload = generate_transaction_data(master_payload, scale=scale, seed=seed)
    trans_persist_result = persist_transaction_data(trans_payload, reset=reset)
    trans_issues = validate_transaction_data(
        trans_payload,
        master_payload,
        TRANSACTION_SCALES[scale][0],
        TRANSACTION_SCALES[scale][1],
    )

    workflow_stats = None
    if simulate_workflow:
        workflow_stats = simulate_workflow_lifecycle(seed=seed)

    combined_issues = master_issues + trans_issues
    return master_payload, trans_payload, master_persist_result, trans_persist_result, combined_issues, workflow_stats


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Generate SmartFlow master and transaction data.")
    parser.add_argument("--scale", choices=("starter", "full"), default="full")
    parser.add_argument(
        "--reset",
        action="store_true",
        default=False,
        help="Replace existing data. Never used by default.",
    )
    parser.add_argument("--seed", type=int, default=RANDOM_SEED)
    parser.add_argument(
        "--simulate-workflow",
        action="store_true",
        default=False,
        help="Simulate end-to-end approval routing and reimbursement lifecycle.",
    )
    parser.add_argument(
        "--target-db",
        type=str,
        default=None,
        help="Target SQLite database path for isolated data generation.",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.target_db:
        from pathlib import Path
        db_path = Path(args.target_db).resolve().as_posix()
        os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    master_payload, trans_payload, master_persist, trans_persist, issues, workflow_stats = generate_all(
        seed=args.seed,
        scale=args.scale,
        reset=args.reset,
        simulate_workflow=args.simulate_workflow,
        target_db=args.target_db,
    )
    print_summary(master_payload, trans_payload, master_persist, trans_persist, issues, args.scale, workflow_stats)
    if issues:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
