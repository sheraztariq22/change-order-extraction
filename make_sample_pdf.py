"""Builds samples/co_005_pdf.pdf (a text-layer PDF) from a .txt sample, with no third-party deps."""
import sys
from pathlib import Path


def build(lines, out):
    bs = chr(92)
    esc = lambda s: s.replace(bs, bs * 2).replace("(", bs + "(").replace(")", bs + ")")
    stream = "BT /F1 10 Tf 12 TL 50 760 Td\n" + "\n".join(f"({esc(l)}) '" for l in lines) + "\nET"
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
            "/Resources << /Font << /F1 5 0 R >> >> >>",
            f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>"]
    buf, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(buf))
        buf += f"{i} 0 obj\n{o}\nendobj\n".encode("latin-1")
    x = len(buf)
    buf += f"xref\n0 {len(objs)+1}\n0000000000 65535 f \n".encode()
    buf += "".join(f"{o:010d} 00000 n \n" for o in offs).encode()
    buf += f"trailer\n<< /Size {len(objs)+1} /Root 1 0 R >>\nstartxref\n{x}\n%%EOF\n".encode()
    Path(out).write_bytes(buf)


if __name__ == "__main__":
    src = Path(sys.argv[1] if len(sys.argv) > 1 else "samples/co_001_clean.txt")
    build(src.read_text(encoding="utf-8").splitlines(), sys.argv[2] if len(sys.argv) > 2 else "samples/co_005_pdf.pdf")
