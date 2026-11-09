"""Turn a PDF or text file into normalised plain text (one string, line structure kept)."""
from __future__ import annotations

import re
from pathlib import Path

_TRANSLATE = {
    " ": " ", " ": " ", "​": "", "﻿": "", "\f": "\n",
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "−": "-", "–": "-", "—": "-", "\t": "   ", "\r": "",
}


def normalise(text: str) -> str:
    for k, v in _TRANSLATE.items():
        text = text.replace(k, v)
    text = re.sub(r"[ ]+$", "", text, flags=re.M)
    return re.sub(r"\n{4,}", "\n\n\n", text).strip("\n")


def read_pdf(path: Path) -> tuple[str, list[str]]:
    warnings: list[str] = []
    try:
        from pypdf import PdfReader
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("pypdf is required for PDF input: pip install pypdf") from e
    reader = PdfReader(str(path))
    pages = []
    for p in reader.pages:
        try:
            pages.append(p.extract_text(extraction_mode="layout") or "")
        except TypeError:  # older pypdf
            pages.append(p.extract_text() or "")
    text = "\n".join(pages)
    if len(re.sub(r"\s", "", text)) < 40:
        warnings.append("no_text_layer: PDF looks scanned/image-only; OCR is required (not bundled)")
    return text, warnings


def read_any(path: str | Path) -> tuple[str, list[str]]:
    path = Path(path)
    if path.suffix.lower() == ".pdf":
        text, warnings = read_pdf(path)
    else:
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text, warnings = raw.decode("latin-1"), ["decoded_as_latin1"]
        else:
            warnings = []
    return normalise(text), warnings
