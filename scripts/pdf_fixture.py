"""Generate large, valid PDFs for the P1 demo and tests.

Real PDFs, not random bytes: the file that moves between the two agents should be
something a person could open and read, because that is what P1 promises. Each
page carries a text line plus a block of `%` comments inside its content stream,
which is legal PDF syntax and inflates the file past 1 MB without making it
unreadable.
"""

from __future__ import annotations

from pathlib import Path


def pdf_bytes(label: str, padding_bytes: int = 1_300_000, pages: int = 2) -> bytes:
    """A minimal valid PDF with `pages` pages and roughly `padding_bytes` of bulk."""
    per_page = max(0, padding_bytes // max(1, pages))
    catalog_num, pages_num, font_num = 1, 2, 3
    page_nums = [4 + 2 * i for i in range(pages)]
    content_nums = [5 + 2 * i for i in range(pages)]

    objects: dict[int, bytes] = {}
    objects[catalog_num] = b"<< /Type /Catalog /Pages %d 0 R >>" % pages_num
    kids = b" ".join(b"%d 0 R" % n for n in page_nums)
    objects[pages_num] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, pages)
    objects[font_num] = (
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
        b"/Encoding /WinAnsiEncoding >>"
    )

    for index, (page_num, content_num) in enumerate(zip(page_nums, content_nums), start=1):
        text = f"{label} - page {index} of {pages}"
        body = (
            b"BT /F1 18 Tf 72 760 Td (" + text.encode("latin-1") + b") Tj ET\n"
        )
        # Comment lines pad the stream; a viewer skips them.
        line = b"%" + b"-" * 78 + b"\n"
        if per_page:
            body += line * (per_page // len(line))
        objects[content_num] = (
            b"<< /Length %d >>\nstream\n" % len(body) + body + b"\nendstream"
        )
        objects[page_num] = (
            b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 595 842] "
            b"/Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>"
            % (pages_num, font_num, content_num)
        )

    count = max(objects) + 1
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0] * count
    for num in sorted(objects):
        offsets[num] = len(out)
        out += b"%d 0 obj\n" % num + objects[num] + b"\nendobj\n"

    xref_at = len(out)
    out += b"xref\n0 %d\n" % count
    out += b"0000000000 65535 f \n"
    for num in range(1, count):
        out += b"%010d 00000 n \n" % offsets[num]
    out += (
        b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n"
        % (count, catalog_num, xref_at)
    )
    return bytes(out)


def write_pdf(path: str | Path, label: str, padding_bytes: int = 1_300_000) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pdf_bytes(label, padding_bytes=padding_bytes))
    return path
