from pathlib import Path

import pytest

from co_extract import extract_file, extract_text
from co_extract.parsing import parse_date, parse_money

S = Path(__file__).parent.parent / "samples"


def val(r, k):
    return r["fields"][k]["value"]


def conf(r, k):
    return r["fields"][k]["confidence"]


def test_clean_document_extracts_everything_and_reconciles():
    r = extract_file(S / "co_001_clean.txt")
    assert val(r, "change_order_number") == "007"
    assert val(r, "date") == "2024-03-14"
    assert val(r, "change_amount") == 22825.0
    assert val(r, "revised_contract_sum") == 1318025.0
    assert val(r, "schedule_change_days") == 6
    assert len(r["line_items"]) == 3
    assert all(c["passed"] for c in r["checks"])
    assert r["validation"]["valid"] and not r["needs_review"]
    assert r["overall_confidence"] >= 0.9


def test_ocr_noise_is_repaired_but_flagged_low_confidence():
    r = extract_file(S / "co_002_ocr_noisy.txt")
    assert val(r, "original_contract_sum") == 4800000.0
    assert val(r, "change_amount") == 38910.5
    assert any("ocr_repair" in i for i in r["fields"]["change_amount"]["issues"])
    assert conf(r, "change_amount") < 0.75
    assert val(r, "description") is None  # 'Descripti0n' label is not recognised
    assert r["needs_review"] and not r["validation"]["valid"]
    assert any("ambiguous" in i for i in r["fields"]["date"]["issues"])


def test_arithmetic_mismatch_is_an_error_with_sign_hint():
    r = extract_file(S / "co_003_deduct_bad_math.txt")
    assert val(r, "change_amount") == -15000.0
    assert not r["validation"]["valid"]
    assert "sign_may_be_flipped" in r["fields"]["change_amount"]["issues"]
    assert conf(r, "change_amount") < 0.5
    assert val(r, "schedule_change_days") == 0


def test_informal_text_never_invents_missing_fields():
    r = extract_file(S / "co_004_informal.txt")
    assert val(r, "change_order_number") == "22"
    assert val(r, "project_name") is None and conf(r, "project_name") == 0.0
    assert val(r, "change_amount") == 64200.0 and conf(r, "change_amount") <= 0.5
    assert r["needs_review"]


def test_conflicting_amounts_reduce_confidence():
    base = "Change Order No. 1\nProject: X\nDate: 2024-01-02\nDescription: stuff\n"
    one = extract_text(base + "Change Order Amount: $5,000.00")
    two = extract_text(base + "Change Order Amount: $5,000.00\nNet change: $7,500.00")
    assert conf(two, "change_amount") < conf(one, "change_amount")
    assert any("conflicting" in i for i in two["fields"]["change_amount"]["issues"])


def test_empty_input_is_invalid_not_a_crash():
    r = extract_text("")
    assert not r["validation"]["valid"] and r["overall_confidence"] == 0.0 and r["needs_review"]


@pytest.mark.parametrize("s,v", [("$1,234.56", 1234.56), ("($3,200.00)", -3200.0), ("-$500", -500.0),
                                  ("$ 38,9l0.50", 38910.5), ("12,000", 12000.0)])
def test_money(s, v):
    assert float(parse_money(s).value) == v


@pytest.mark.parametrize("s,iso", [("March 14, 2024", "2024-03-14"), ("14 Mar 2024", "2024-03-14"),
                                    ("2024-03-14", "2024-03-14"), ("03/14/2024", "2024-03-14"),
                                    ("14/03/2024", "2024-03-14")])
def test_dates(s, iso):
    assert parse_date(s).value == iso


def test_pdf_matches_text_extraction(tmp_path):
    import sys
    sys.path.insert(0, str(S.parent))
    from make_sample_pdf import build
    pdf = tmp_path / "co.pdf"
    build((S / "co_001_clean.txt").read_text(encoding="utf-8").splitlines(), pdf)
    a, b = extract_file(pdf), extract_file(S / "co_001_clean.txt")
    assert {k: f["value"] for k, f in a["fields"].items()} == {k: f["value"] for k, f in b["fields"].items()}


def test_image_only_pdf_flags_missing_text_layer(tmp_path):
    import sys
    sys.path.insert(0, str(S.parent))
    from make_sample_pdf import build
    pdf = tmp_path / "scan.pdf"
    build([], pdf)
    r = extract_file(pdf)
    assert any("no_text_layer" in w for w in r["warnings"]) and not r["validation"]["valid"]
