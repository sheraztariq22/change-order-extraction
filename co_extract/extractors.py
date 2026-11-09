"""Field extractors. Each returns a list of Candidate objects; the pipeline resolves conflicts."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from .parsing import parse_date, parse_money


@dataclass
class Candidate:
    value: object
    conf: float
    line: int  # 0-based
    evidence: str
    issues: list[str] = field(default_factory=list)


def _ev(line: str) -> str:
    line = re.sub(r"\s{2,}", "  ", line.strip())
    return line[:160]


_NEG_WORDS = re.compile(r"\b(deduct\w*|credit|decrease\w*|reduction|savings)\b", re.I)
_SEP = r"\s*[:\-]\s*"


def _next_nonempty(lines, i):
    for j in range(i + 1, min(i + 3, len(lines))):
        if lines[j].strip():
            return j
    return None


# ---------------------------------------------------------------- text fields
def _clean_value(v: str) -> str:
    v = re.split(r"\s{3,}|\||\s+(?:Contractor|Owner|Project|Date|Architect|Contract\s+No)\s*:", v)[0]
    return v.strip(" \t.,;:-")


def text_field(lines, label_re, base=0.92, multiline=False, maxlen=140) -> list[Candidate]:
    rx = re.compile(r"(?:^|\s{3,}|\|)\W*(?:%s)%s(.*)$" % (label_re, _SEP), re.I)
    out = []
    for i, line in enumerate(lines):
        m = rx.search(line)
        if not m:
            continue
        val, conf, issues, ln = m[1], base, [], i
        if multiline:
            parts = [val.strip()] if val.strip() else []
            for j in range(i + 1, min(i + 13, len(lines))):
                nxt = lines[j]
                if not nxt.strip() or re.match(r"^\W*[A-Za-z][A-Za-z ./#]{2,40}\s*:\s", nxt):
                    break
                parts.append(nxt.strip())
            val = " ".join(parts)
            if len(val) > 1500:
                val, conf = val[:1500], conf * 0.8
                issues.append("truncated")
        else:
            val = _clean_value(val)
        if not val.strip():
            j = _next_nonempty(lines, i)
            if j is None:
                continue
            val = lines[j] if multiline else _clean_value(lines[j])
            conf, ln = conf * 0.75, j
            issues.append("value_on_next_line")
        val = re.sub(r"\s+", " ", val).strip()
        if len(val) < 2:
            continue
        if not multiline and len(val) > maxlen:
            conf *= 0.6
            issues.append("suspiciously_long")
        out.append(Candidate(val, conf, ln, _ev(lines[ln]), issues))
    return out


# ------------------------------------------------------------------ CO number
_CO_PATTERNS = [
    (re.compile(r"\b(?:change\s*order|C\.?O\.?|PCO|CCO)\s*(?:no\.?|number|num|#)\s*[:#.\-]?\s*"
                r"([A-Z]{0,4}-?\d{1,6}[A-Z]?)\b", re.I), 0.94),
    (re.compile(r"\b((?:P|C)?CO-\d{1,6}[A-Z]?)\b", re.I), 0.85),
    (re.compile(r"\bCO\s*[#:]?\s*(\d{1,6})\b"), 0.7),  # case-sensitive on purpose
    (re.compile(r"\bchange\s*order\s*[#:]?\s*-?\s*(\d{1,6})\b(?!\s*(?:days?|%|,\d))", re.I), 0.65),
]


def change_order_number(lines) -> list[Candidate]:
    out = []
    for i, line in enumerate(lines):
        for rx, base in _CO_PATTERNS:
            for m in rx.finditer(line):
                v = re.sub(r"\s+", "", m[1]).upper()
                out.append(Candidate(v, base - (0.04 if i > 15 else 0), i, _ev(line)))
    return out


# ----------------------------------------------------------------------- dates
_DATE_LABEL = re.compile(
    r"(?:^|\s{3,}|\|)\W*(?:change\s+order\s+|co\s+|issue\s+|issued\s+)?"
    r"(?:date(?:\s+of\s+(?:issuance|issue|change\s+order))?|dated)\b\s*[:\-.]*\s*(.*)$",
    re.I)


def date_field(lines) -> list[Candidate]:
    out = []
    for i, line in enumerate(lines):
        m = _DATE_LABEL.search(line)
        if not m:
            continue
        p = parse_date(m[1])
        ln = i
        extra = 1.0
        if not p and (j := _next_nonempty(lines, i)) is not None:
            p, ln, extra = parse_date(lines[j]), j, 0.8
        if p:
            out.append(Candidate(p.value, 0.93 * p.mult * extra, ln, _ev(lines[ln]), p.issues))
    if not out:  # weak fallback: first date in the header region
        for i, line in enumerate(lines[:40]):
            p = parse_date(line)
            if p:
                out.append(Candidate(p.value, 0.45 * p.mult, i, _ev(line), p.issues + ["unlabeled_date"]))
                break
    return out


# ---------------------------------------------------------------------- money
def money_field(lines, label_re, base=0.9, direction=False) -> list[Candidate]:
    rx = re.compile(label_re, re.I)
    out = []
    for i, line in enumerate(lines):
        m = rx.search(line)
        if not m:
            continue
        ln, mult, issues = i, 1.0, []
        rest = line[m.end():]
        sign = 1
        phrase_dir = m.groupdict().get("dir")
        if phrase_dir:
            d = phrase_dir.lower()
            sign = -1 if d == "decreased" else 1
            if d == "unchanged":
                out.append(Candidate(Decimal("0.00"), base, i, _ev(line), []))
                continue
        p = parse_money(rest)
        if not p and (j := _next_nonempty(lines, i)) is not None:
            p, ln, mult = parse_money(lines[j]), j, 0.85
            if p:
                issues.append("value_on_next_line")
        if not p:
            continue
        val = p.value * sign
        if phrase_dir:
            issues.append(f"sign_from_phrase:{phrase_dir.lower()}")
        elif direction and val > 0 and _NEG_WORDS.search(line + " " + (lines[i - 1] if i else "")):
            val, mult = -val, mult * 0.85
            issues.append("sign_inferred_from_keyword")
        out.append(Candidate(val.quantize(Decimal("0.01")), base * p.mult * mult, ln, _ev(lines[ln]),
                             issues + p.issues))
    return out


ORIGINAL_RE = r"(?:original\s+contract\s+(?:sum|amount|price|value)|contract\s+sum\s+was)"
PRIOR_RE = (r"(?:net\s+change\s+by\s+previous\w*\s+(?:authori[sz]ed\s+)?change\s+orders?"
            r"|previous(?:ly\s+approved)?\s+change\s+orders?(?:\s+total)?)")
REVISED_RE = r"(?:(?:new|revised|adjusted|current|updated)\s+contract\s+(?:sum|amount|price|value))"
CHANGE_RES = [
    (r"(?:contract\s+(?:sum|price|amount))\s+(?:will\s+be|is|shall\s+be)\s+(?P<dir>increased|decreased|unchanged)\s+by"
     r"(?:\s+this\s+change\s+order)?(?:\s+in\s+the\s+amount\s+of)?", 0.93),
    (r"(?:amount\s+of\s+(?:this\s+)?change\s+order|(?:this\s+)?change\s+order\s+(?:amount|total|price|cost)"
     r"|total\s+(?:of\s+this\s+)?change(?:\s+order)?|net\s+change(?!\s+by\s+previous)"
     r"|change\s+in\s+contract\s+(?:sum|price|amount)|total\s+(?:add|deduct)(?:ion)?)", 0.9),
    (r"^\W*total\s*[:.]", 0.5),
]


def change_amount(lines):
    out = []
    for rx, base in CHANGE_RES:
        out += money_field(lines, rx, base, direction=True)
    return out


# --------------------------------------------------------------------- schedule
_SCHED_A = re.compile(
    r"\b(?:contract\s+time|completion\s+date|schedule|duration|time)\b[^.]{0,60}?"
    r"\b(increased|decreased|extended|reduced|shortened)\b"
    r"[^.]{0,25}?\bby\s+(\d{1,4})\s*(?:\(\d+\)\s*)?(?:calendar|working|business|work)?\s*days?", re.I)
_SCHED_B = re.compile(
    r"\b(?:time\s+extension|extension\s+of\s+time|schedule\s+impact|additional\s+time|days?\s+added|"
    r"contract\s+time\s+change|change\s+in\s+(?:contract\s+)?time)\s*[:\-]?\s*(\+|-)?\s*(\d{1,4})"
    r"\s*(?:calendar|working)?\s*days?", re.I)
_SCHED_C = re.compile(
    r"\bno\s+(?:change|impact|extension|adjustment)\s+(?:in|to|of|on)\s+(?:the\s+)?(?:contract\s+)?(?:time|schedule)"
    r"|\b(?:contract\s+time|schedule)\s+(?:will\s+be|is|shall\s+be|remains?)\s+unchanged", re.I)


def schedule_days(text) -> list[Candidate]:
    flat = text.replace("\n", " ")  # same offsets, so line numbers stay recoverable

    def ev(m):
        return _ev(flat[max(0, m.start() - 20): m.end() + 20])

    def ln(m):
        return text.count("\n", 0, m.start())

    out = []
    for m in _SCHED_A.finditer(flat):
        n = int(m[2])
        neg = m[1].lower() in ("decreased", "reduced", "shortened")
        out.append(Candidate(-n if neg else n, 0.88, ln(m), ev(m)))
    for m in _SCHED_B.finditer(flat):
        n = int(m[2])
        out.append(Candidate(-n if m[1] == "-" else n, 0.85, ln(m), ev(m)))
    for m in _SCHED_C.finditer(flat):
        out.append(Candidate(0, 0.85, ln(m), ev(m)))
    return out


# ------------------------------------------------------------------- line items
_LINE_ITEM = re.compile(
    r"^\s*(?:\d+[.)]|[-*•]|[A-Za-z][.)])?\s*(?P<desc>[A-Za-z][^$\n]{3,100}?)[\s.:_-]{2,}"
    r"(?P<amt>\(?-?\s*\$\s*[\d,]+(?:\.\d{1,2})?\)?)\s*$")
_NOT_ITEM = re.compile(
    r"contract\s+(?:sum|price|amount)|total|subtotal|net\s+change|grand|change\s+order\s+(?:amount|total)|"
    r"previous|balance|amount\s+of", re.I)


def line_items(lines) -> list[Candidate]:
    out = []
    for i, line in enumerate(lines):
        m = _LINE_ITEM.match(line)
        if not m or _NOT_ITEM.search(m["desc"]):
            continue
        p = parse_money(m["amt"])
        if not p:
            continue
        out.append(Candidate({"description": re.sub(r"\s+", " ", m["desc"]).strip(),
                              "amount": p.value.quantize(Decimal("0.01"))},
                             0.72 * p.mult, i, _ev(line), p.issues))
    return out
