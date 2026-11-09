"""Money and date parsing. Each parser returns the value plus a confidence multiplier and issue tags."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

_TOK = re.compile(
    r"(?P<open>\()?\s*(?P<s1>-)?\s*(?P<dollar>\$)?\s*(?P<s2>-)?\s*"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)(?![\d,])\s*(?P<close>\))?"
)
_OCR = re.compile(r"\$\s*([0-9OolIS][0-9OolIS,.]*[0-9OolIS])")
_OCR_MAP = str.maketrans({"O": "0", "o": "0", "l": "1", "I": "1", "S": "5"})


@dataclass
class Parsed:
    value: object
    mult: float = 1.0
    issues: list[str] = field(default_factory=list)


_MONEY_SHAPE = re.compile(r"\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?")


def parse_money(s: str) -> Parsed | None:
    # OCR confusions (O/0, l/1, S/5) inside a $ amount: repair first, but only if the result is money-shaped
    m = _OCR.search(s)
    if m and re.search(r"[OolIS]", m[1]):
        fixed = m[1].translate(_OCR_MAP)
        if _MONEY_SHAPE.fullmatch(fixed):
            return Parsed(Decimal(fixed.replace(",", "")), 0.6, [f"ocr_repair: {m[1]!r} -> {fixed}"])
    best = None
    for m in _TOK.finditer(s):
        num = m["num"]
        tier = 1 if m["dollar"] else (2 if ("," in num or "." in num) else 3)
        if best is None or tier < best[0]:
            best = (tier, m)
    if best:
        tier, m = best
        val = Decimal(m["num"].replace(",", ""))
        if (m["open"] and m["close"]) or m["s1"] or m["s2"]:
            val = -val
        return Parsed(val, {1: 1.0, 2: 0.9, 3: 0.75}[tier], [] if tier < 3 else ["no_currency_symbol"])
    m = _OCR.search(s)
    if m:
        fixed = m[1].translate(_OCR_MAP).replace(",", "")
        try:
            val = Decimal(fixed)
        except Exception:
            return None
        return Parsed(val, 0.6, [f"ocr_repair: {m[1]!r} -> {fixed}"])
    return None


_MONTHS = {m: i + 1 for i, m in enumerate(
    "jan feb mar apr may jun jul aug sep oct nov dec".split())}
_ISO = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_NUM = re.compile(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4}|\d{2})\b")
_MDY = re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b")
_DMY = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})\b")


def _mk(y, m, d):
    try:
        return date(int(y), int(m), int(d))
    except ValueError:
        return None


def parse_date(s: str) -> Parsed | None:
    if m := _ISO.search(s):
        if d := _mk(*m.groups()):
            return Parsed(d.isoformat())
    if m := _MDY.search(s):
        mo = _MONTHS.get(m[1][:3].lower())
        if mo and (d := _mk(m[3], mo, m[2])):
            return Parsed(d.isoformat())
    if m := _DMY.search(s):
        mo = _MONTHS.get(m[2][:3].lower())
        if mo and (d := _mk(m[3], mo, m[1])):
            return Parsed(d.isoformat())
    if m := _NUM.search(s):
        a, b, y = int(m[1]), int(m[2]), m[3]
        issues, mult = [], 1.0
        if len(y) == 2:
            y, mult = "20" + y, mult * 0.85
            issues.append("two_digit_year")
        if a > 12 >= b:
            d, mult = _mk(y, b, a), mult * 0.9
            issues.append("interpreted_as_DD/MM")
        else:
            d = _mk(y, a, b)
            if d and b <= 12 and a != b:
                mult *= 0.8
                issues.append("ambiguous_MM/DD_vs_DD/MM (assumed US)")
        if d:
            return Parsed(d.isoformat(), mult, issues)
    return None
