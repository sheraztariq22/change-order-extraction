# Change-order extraction pipeline

Turns messy change-order PDFs or text into validated JSON. Every field carries a confidence score, the source line it came from, and a list of issue tags. Documents that fail validation or contain weak fields are flagged `needs_review`.

```
python -m co_extract samples/co_001_clean.txt          # one file -> JSON on stdout
python -m co_extract samples/*.txt samples/*.pdf --out outputs
python -m pytest -q                                    # 18 tests
```
Requires Python 3.10+. `pypdf` is needed only for PDF input; everything else is stdlib. Exit code is 1 if any record fails validation.

## Output

```jsonc
{
  "source": "co_003_deduct_bad_math.txt",
  "fields": {
    "change_amount": {"value": -15000.0, "confidence": 0.421, "line": 12,
                      "evidence": "Total Deduct:  $15,000.00",
                      "issues": ["sign_inferred_from_keyword", "fails_contract_sum_arithmetic", "sign_may_be_flipped"]},
    "...": "13 fields: change_order_number, project_name, project_number, date, owner, contractor, description,
             reason_for_change, original_contract_sum, prior_change_orders_total, change_amount,
             revised_contract_sum, schedule_change_days"
  },
  "line_items": [{"description": "...", "amount": 12450.0, "confidence": 0.72, "line": 9}],
  "checks":     [{"name": "contract_sum_arithmetic", "passed": false, "detail": "2000000.00 + 0 + -15000.00 = 1985000.00 vs stated 2015000.00"}],
  "validation": {"valid": false, "errors": ["..."], "warnings": ["..."]},
  "overall_confidence": 0.421,   // min over the 5 required fields
  "needs_review": true, "review_reasons": ["change_amount", "..."]
}
```
Missing fields are `value: null, confidence: 0.0, issues: ["not_found"]`. The pipeline never guesses a value to fill a gap. Dates are ISO-8601 and money values are numbers (computed internally with `Decimal`).

## Approach

1. **Ingest** (`ingest.py`): PDF text via `pypdf` layout mode, which keeps column gaps so table rows stay on one line. Text is normalised (smart quotes, unicode minus/dashes, nbsp). A PDF with no text layer is reported as `no_text_layer` instead of returning silent empties.
2. **Candidate extraction** (`extractors.py`): label-driven regexes, with many label variants per field (AIA G701 wording such as "The Contract Sum will be increased by this Change Order in the amount of", "Net change", "Total Deduct", "CO #", "PCO-014"). Each pattern yields *candidates*, not answers. Each candidate has a base confidence reflecting how specific its pattern is. For example, `Change Order Amount: $` scores 0.90 and a bare `Total:` fallback scores 0.50.
3. **Parsing** (`parsing.py`): money handles `$`, commas, `(parens)` and `-` as negatives, and OCR confusions (`O→0, l→1, S→5`) inside `$` amounts, accepted only if the result is money-shaped. Dates handle ISO, `March 14, 2024`, `14 Mar 2024` and numeric forms. Ambiguous `03/04/2024` is assumed US and penalised.
4. **Resolution** (`pipeline.resolve`): the best candidate wins. Corroboration (the same value found in two different places) adds +0.04. A credible rival value (conf ≥ 0.6) multiplies confidence by 0.8, or by 0.6 if it is close in strength. Two patterns firing on the same line count as one source.
5. **Cross-field reconciliation**: `original + prior_changes + change == revised` and `Σ line items == change_amount`. A pass raises confidence on the involved fields. A failure scales it down by ×0.55 and records an error. If flipping the sign would make the arithmetic work, a `sign_may_be_flipped` hint is added.
6. **Validation** (`validate.py`): type/shape rules, required fields, ISO dates, a positive original sum, and the arithmetic check. Arithmetic failure is an error, and a document with errors is never marked clean.
7. **Document-level penalty**: if more than 2% of characters are non-text junk (a typical OCR symptom), all confidences are scaled down by up to 35%.

**Confidence is a heuristic score, not a calibrated probability.** It is built from pattern specificity, repair penalties, corroboration, conflicts and arithmetic checks, and it was tuned by hand on 5 samples. With labelled data, the next step is to fit a calibration curve (isotonic regression) per field and set review thresholds from the observed precision.

## Failure modes (known, with what the pipeline does about each)

| Failure | What happens now | Mitigation / next step |
|---|---|---|
| **Scanned/image PDFs** | `no_text_layer` warning, all required fields missing, invalid | Add OCR (Tesseract or a vision LLM) in `ingest.py`. The OCR-repair and noise penalties already exist for its output. |
| **OCR'd label typos** (`Descripti0n`) | Field returned as `null`, flagged for review (see `co_002`) | Fuzzy label matching, or an LLM fallback for fields that stay empty. |
| **OCR digit errors that stay money-shaped** (`$1,250,000` read as `$1,260,000`) | Not detectable alone. The arithmetic check often catches it, and the noise penalty lowers confidence. | Only reconciliation or a second reader can catch it. This is the most dangerous failure because the value looks valid. |
| **Sign errors** (deduct vs. add; credits shown as positive) | Sign is inferred from keywords like "deduct/credit" at ×0.85, or from "increased/decreased by" phrasing. Arithmetic mismatch plus `sign_may_be_flipped` surfaces it. | Rely on the arithmetic check. Never trust sign from a keyword alone. |
| **Two values for one field** (revisions, "was/now", multiple COs in one PDF) | Best candidate kept, conflict penalty applied, rivals listed in `issues` | Segment multi-CO documents first. The current pipeline assumes one change order per document. |
| **Values far from their label** (multi-column or multi-page PDFs where layout extraction reorders text) | Next-line fallback only (0.85×/0.75×). Otherwise missed or mis-assigned. | Layout-aware parsing (pdfplumber table or word-box extraction), or a vision model. |
| **Ambiguous dates** (`03/04/2024`) | Assumed US month/day, ×0.8, flagged. When one part is >12 the correct reading is used. | Take the locale from the contract or the jurisdiction. |
| **Free-form emails and prose** (see `co_004`) | CO number and amount may be found with low confidence. Project name and description are often missing and are never invented. | An LLM extractor is the right tool here. Keep the rule-based confidence and arithmetic checks as the verifier around it. |
| **Line items that include tax/markup rows or are incomplete** | `line_items_sum_mismatch` warning and a small confidence reduction (not an error) | Line-item layouts vary too much for a hard rule. |
| **Duplicate labels** (a "Description" column header or boilerplate that matches `description:`) | May produce a wrong description at moderate confidence | Section-aware parsing. |
| **Non-English, non-US currency formats** (`1.234,56`) | Misparsed (e.g. as 1.23). Not handled. | Locale detection. |
| **Over-confidence on a single clean-looking wrong match** | A single unconfirmed field can reach about 0.9 even if wrong | This is the cost of an uncalibrated score. Calibrate against labelled data before using the score to auto-approve anything. |

## Design choices

- **Rules, not an LLM, as the core.** Output is deterministic, auditable (each value cites its source line) and free to run. The weak spots are free-form prose and layout, so the intended production design wraps the same candidate and verify interface around an LLM or vision-model extractor. Its output would pass through the same reconciliation, validation and `needs_review` logic. That path is not implemented here.
- **Confidence is penalised, never value-edited.** The only value derivations are explicit sign rules, and each one is tagged in `issues`.
- **Fail loudly.** Missing required fields, arithmetic mismatches and unreadable PDFs become validation errors and `needs_review`, not silent defaults.

## Layout

```
co_extract/ingest.py      PDF/text -> normalised text
co_extract/parsing.py     money + date parsers
co_extract/extractors.py  label/regex candidate extractors
co_extract/pipeline.py    resolve, reconcile, score, assemble
co_extract/validate.py    schema + business-rule validation
samples/                  5 inputs: clean, OCR-noisy, bad arithmetic, informal email, PDF
outputs/                  JSON produced from the samples
tests/                    18 tests
make_sample_pdf.py        dependency-free PDF generator used by tests and samples
```

## Limits of this submission

Tested on 5 hand-written samples that I made, not on real change orders. It will not generalise to arbitrary vendor formats without more patterns, and accuracy on real documents is unmeasured.
