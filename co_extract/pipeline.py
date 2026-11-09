"""Pipeline: text -> candidates -> resolved fields -> cross-checks -> validation -> JSON record."""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

from . import extractors as ex
from .ingest import normalise, read_any
from .validate import validate_record

REQUIRED = ["change_order_number", "project_name", "date", "description", "change_amount"]
MONEY = ["original_contract_sum", "prior_change_orders_total", "change_amount", "revised_contract_sum"]
FIELD_ORDER = ["change_order_number", "project_name", "project_number", "date", "owner", "contractor",
                "description", "reason_for_change", "original_contract_sum", "prior_change_orders_total",
                "change_amount", "revised_contract_sum", "schedule_change_days"]
REVIEW_BELOW = 0.75  # required field below this -> human review
TOL = Decimal("0.01")


def _clip(c: float) -> float:
    return round(max(0.0, min(0.99, c)), 3)


def _json_val(v):
    return float(v) if isinstance(v, Decimal) else v


def resolve(cands):
    """Pick the best candidate; reward corroboration, penalise credible conflicting values."""
    if not cands:
        return None
    dedup = {}
    for c in cands:  # same line + same value found by two patterns is not corroboration
        k = (c.line, str(c.value))
        if k not in dedup or c.conf > dedup[k].conf:
            dedup[k] = c
    groups = {}
    for c in dedup.values():
        groups.setdefault(str(c.value), []).append(c)
    ranked = sorted(groups.values(), key=lambda g: max(c.conf for c in g), reverse=True)
    best = max(ranked[0], key=lambda c: c.conf)
    conf, issues = best.conf, list(best.issues)
    if len(ranked[0]) > 1:
        conf += 0.04
        issues.append(f"corroborated_x{len(ranked[0])}")
    rivals = [g for g in ranked[1:] if max(c.conf for c in g) >= 0.6]
    if rivals:
        close = max(max(c.conf for c in g) for g in rivals) >= best.conf - 0.15
        conf *= 0.6 if close else 0.8
        issues.append("conflicting_values: " + ", ".join(str(g[0].value)[:40] for g in rivals[:3]))
    return {"value": best.value, "confidence": conf, "evidence": best.evidence,
            "line": best.line + 1, "issues": issues}


def _empty():
    return {"value": None, "confidence": 0.0, "evidence": None, "line": None, "issues": ["not_found"]}


def _adj(f, mult=1.0, add=0.0, issue=None):
    if f and f["value"] is not None:
        f["confidence"] = f["confidence"] * mult + add
        if issue:
            f["issues"].append(issue)


def _noise(text: str) -> float:
    ok = set(".,:;$()#/-&'\"%@+*[]_|!?•")
    bad = sum(1 for ch in text if not (ch.isalnum() or ch.isspace() or ch in ok))
    return bad / max(len(text), 1)


def extract_text(text: str, source: str = "<text>", ingest_warnings: list[str] | None = None) -> dict:
    text = normalise(text)
    lines = text.split("\n")
    warnings = list(ingest_warnings or [])

    F = {
        "change_order_number": resolve(ex.change_order_number(lines)),
        "project_name": resolve(ex.text_field(lines, r"project(?:\s+name|\s+title)?")),
        "project_number": resolve(ex.text_field(lines, r"project\s*(?:no\.?|number|#)", maxlen=40)),
        "date": resolve(ex.date_field(lines)),
        "owner": resolve(ex.text_field(lines, r"owner|client")),
        "contractor": resolve(ex.text_field(lines, r"(?:general\s+)?contractor")),
        "description": resolve(ex.text_field(
            lines, r"description\s+of\s+(?:change|work)|change\s+description|scope\s+of\s+(?:change|work)"
                   r"|work\s+description|description|scope", multiline=True)),
        "reason_for_change": resolve(ex.text_field(lines, r"reason(?:\s+for\s+change)?", multiline=True)),
        "original_contract_sum": resolve(ex.money_field(lines, ex.ORIGINAL_RE)),
        "prior_change_orders_total": resolve(ex.money_field(lines, ex.PRIOR_RE, 0.88)),
        "change_amount": resolve(ex.change_amount(lines)),
        "revised_contract_sum": resolve(ex.money_field(lines, ex.REVISED_RE)),
        "schedule_change_days": resolve(ex.schedule_days(text)),
    }
    F = {k: (v or _empty()) for k, v in F.items()}
    items = [c for c in ex.line_items(lines)]
    checks = []

    # ---- cross-field reconciliation (adjusts confidence, never silently changes values)
    O, P, C, R = (F[k]["value"] for k in MONEY)
    if None not in (O, C, R):
        exp = O + (P or Decimal(0)) + C
        ok = abs(exp - R) <= TOL
        detail = f"{O} + {P or 0} + {C} = {exp} vs stated {R}"
        checks.append({"name": "contract_sum_arithmetic", "passed": ok, "detail": detail})
        involved = [k for k in MONEY if F[k]["value"] is not None]
        for k in involved:
            if ok:
                _adj(F[k], add=0.05)
            else:
                _adj(F[k], mult=0.55, issue="fails_contract_sum_arithmetic")
        if not ok and abs(O + (P or Decimal(0)) - C - R) <= TOL:
            F["change_amount"]["issues"].append("sign_may_be_flipped")
    elif C is not None:
        warnings.append("cannot_cross_check_arithmetic: original/revised contract sum not both found")

    if len(items) >= 2 and C is not None:
        total = sum((c.value["amount"] for c in items), Decimal(0))
        ok = abs(total - C) <= TOL
        checks.append({"name": "line_items_sum_to_change_amount", "passed": ok,
                       "detail": f"sum(items)={total} vs change_amount={C}"})
        if ok:
            _adj(F["change_amount"], add=0.03)
        else:
            _adj(F["change_amount"], mult=0.92, issue="line_items_do_not_sum_to_change_amount")
            warnings.append("line_items_sum_mismatch: items may include markup/tax rows or be incomplete")

    d = F["date"]["value"]
    if d:
        y = int(d[:4])
        plausible = 1990 <= y <= date.today().year + 1
        checks.append({"name": "date_plausible", "passed": plausible, "detail": d})
        if not plausible:
            _adj(F["date"], mult=0.5, issue="implausible_year")

    # ---- document-level quality
    noise = _noise(text)
    if noise > 0.02:
        pen = 1 - min(noise * 3, 0.35)
        warnings.append(f"noisy_text: {noise:.1%} unusual characters (likely OCR); confidences scaled x{pen:.2f}")
        for f in F.values():
            _adj(f, mult=pen)
    if len(text) < 80:
        warnings.append("very_short_input")

    for f in F.values():
        f["confidence"] = _clip(f["confidence"])

    record = {
        "source": source,
        "fields": {k: {**F[k], "value": _json_val(F[k]["value"])} for k in FIELD_ORDER},
        "line_items": [{**c.value, "amount": _json_val(c.value["amount"]),
                        "confidence": _clip(c.conf), "line": c.line + 1} for c in items],
        "checks": checks,
        "warnings": warnings,
    }
    record["validation"] = validate_record(record)
    present = [record["fields"][k]["confidence"] for k in REQUIRED]
    record["overall_confidence"] = round(min(present), 3)  # weakest required field: conservative
    low = [k for k, f in record["fields"].items()
           if f["value"] is not None and f["confidence"] < 0.6]
    weak_req = [k for k in REQUIRED if record["fields"][k]["confidence"] < REVIEW_BELOW]
    record["needs_review"] = bool(record["validation"]["errors"] or weak_req or low)
    record["review_reasons"] = sorted(set(weak_req + low))
    return record


def extract_file(path: str | Path) -> dict:
    text, warns = read_any(path)
    return extract_text(text, source=str(Path(path).name), ingest_warnings=warns)
