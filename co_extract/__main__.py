"""CLI: python -m co_extract FILE [FILE ...] [--out DIR] [--compact]"""
import argparse
import json
import sys
from pathlib import Path

from .pipeline import extract_file


def main(argv=None):
    ap = argparse.ArgumentParser(prog="co_extract", description="Extract change-order data to validated JSON")
    ap.add_argument("files", nargs="+")
    ap.add_argument("--out", help="write <name>.json per input into this directory")
    ap.add_argument("--compact", action="store_true")
    a = ap.parse_args(argv)
    recs = [extract_file(f) for f in a.files]
    dump = lambda o: json.dumps(o, indent=None if a.compact else 2, ensure_ascii=False)
    if a.out:
        Path(a.out).mkdir(parents=True, exist_ok=True)
        for f, r in zip(a.files, recs):
            (Path(a.out) / (Path(f).stem + ".json")).write_text(dump(r), encoding="utf-8")
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(dump(recs[0] if len(recs) == 1 else recs))
    return 0 if all(r["validation"]["valid"] for r in recs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
