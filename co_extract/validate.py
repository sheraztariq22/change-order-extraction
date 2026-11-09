"""Output validation: schema/type rules plus business rules. Returns {"valid", "errors", "warnings"}."""
from __future__ import annotations

import re

REQUIRED = ["change_order_number", "project_name", "date", "description", "change_amount"]
TYPES = {
    "change_order_number": str, "project_name": str, "project_number": str, "date": str, "owner": str,
    "contractor": str, "description": str, "reason_for_change": str,
    "original_contract_sum": (int, float), "prior_change_orders_total": (int, float),
    "change_amount": (int, float), "revised_contract_sum": (int, float), "schedule_change_days": int,
}


def validate_record(rec: dict) -> dict:
    errors, warnings = [], []
    fields = rec["fields"]
    for k, t in TYPES.items():
        f = fields.get(k)
        if f is None:
            errors.append(f"schema: field '{k}' missing from output")
            continue
        v = f["value"]
        if v is not None and (isinstance(v, bool) or not isinstance(v, t)):
            errors.append(f"schema: '{k}' has type {type(v).__name__}")
        if not 0.0 <= f["confidence"] <= 1.0:
            errors.append(f"schema: '{k}' confidence out of range")
    for k in REQUIRED:
        if fields[k]["value"] is None:
            errors.append(f"missing_required: {k}")
    d = fields["date"]["value"]
    if d and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
        errors.append("schema: date is not ISO-8601")
    o = fields["original_contract_sum"]["value"]
    if o is not None and o <= 0:
        errors.append("business: original_contract_sum must be > 0")
    for c in rec["checks"]:
        if not c["passed"] and c["name"] == "contract_sum_arithmetic":
            errors.append(f"business: contract sum arithmetic does not reconcile ({c['detail']})")
        elif not c["passed"]:
            warnings.append(f"{c['name']} failed ({c['detail']})")
    days = fields["schedule_change_days"]["value"]
    if days is not None and abs(days) > 3650:
        warnings.append("schedule_change_days > 10 years")
    ch, rev = fields["change_amount"]["value"], fields["revised_contract_sum"]["value"]
    if ch is not None and o and abs(ch) > 2 * o:
        warnings.append("change_amount exceeds 200% of original contract sum")
    return {"valid": not errors, "errors": errors, "warnings": warnings}
