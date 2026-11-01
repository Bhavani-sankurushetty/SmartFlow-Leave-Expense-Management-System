"""ID format helpers."""


def format_employee_id(sequence):
    return f"EMP-{sequence:06d}"


def format_cost_center_id(sequence):
    return f"CC-{sequence:06d}"


def format_leave_policy_id(sequence):
    return f"LPOL-{sequence:06d}"


def format_leave_balance_id(sequence):
    return f"LBAL-{sequence:06d}"


def format_expense_policy_id(sequence):
    return f"EPOL-{sequence:06d}"


def format_approval_rule_id(sequence):
    return f"ARUL-{sequence:06d}"


def format_leave_request_id(year, sequence):
    return f"LVE-{year}-{sequence:06d}"


def format_expense_claim_id(year, sequence):
    return f"EXP-{year}-{sequence:06d}"


def format_expense_line_id(claim_id, sequence):
    return f"EXPL-{claim_id}-{sequence:02d}"


def format_approval_step_id(request_id, sequence):
    return f"APR-{request_id}-{sequence:02d}"


def format_reimbursement_id(year, sequence):
    return f"RMB-{year}-{sequence:06d}"
