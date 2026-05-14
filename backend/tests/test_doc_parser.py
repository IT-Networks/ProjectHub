"""Tests for the document parser — PDF support (WS-6).

Focuses on the parts testable without a DB session: parser dispatch,
PDF error handling, and a round-trip on a generated text PDF.
"""
import io
import os

import pytest

from services.doc_parser import DocSection, parse_pdf
from services.doc_scanner import SUPPORTED_EXTENSIONS, _parse_document


def _make_text_pdf(pages_text: list[str]) -> bytes:
    """Build a minimal multi-page PDF with a real text layer via pypdf."""
    from pypdf import PdfWriter
    from reportlab.pdfgen import canvas  # type: ignore

    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    for text in pages_text:
        c.drawString(72, 720, text)
        c.showPage()
    c.save()
    buf.seek(0)
    # Round-trip through PdfWriter so the result is a clean PDF.
    writer = PdfWriter(clone_from=buf)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


class TestSupportedExtensions:
    def test_pdf_is_supported(self):
        assert ".pdf" in SUPPORTED_EXTENSIONS
        assert ".docx" in SUPPORTED_EXTENSIONS


class TestParseDocumentDispatch:
    def test_pdf_extension_routes_to_parse_pdf(self, tmp_path):
        # A non-PDF file with a .pdf suffix → parse_pdf returns [] (not a crash).
        fake = tmp_path / "not-really.pdf"
        fake.write_bytes(b"this is not a pdf")
        assert _parse_document(str(fake)) == []


class TestParsePdf:
    def test_missing_file_returns_empty(self):
        assert parse_pdf("C:/nonexistent/nope.pdf") == []

    def test_corrupt_file_returns_empty(self, tmp_path):
        bad = tmp_path / "corrupt.pdf"
        bad.write_bytes(b"%PDF-1.4 broken garbage")
        assert parse_pdf(str(bad)) == []

    def test_text_pdf_yields_one_section_per_page(self, tmp_path):
        reportlab = pytest.importorskip(
            "reportlab", reason="reportlab needed to synthesize a text PDF"
        )
        assert reportlab  # silence linters
        pdf_bytes = _make_text_pdf([
            "Seite eins Inhalt über Architektur",
            "Seite zwei Inhalt über Prozesse",
        ])
        path = tmp_path / "spec.pdf"
        path.write_bytes(pdf_bytes)

        sections = parse_pdf(str(path))
        assert len(sections) == 2
        assert all(isinstance(s, DocSection) for s in sections)
        assert sections[0].heading == "Seite 1"
        assert sections[1].heading == "Seite 2"
        assert sections[0].heading_path == "spec.pdf > Seite 1"
        assert "Architektur" in sections[0].text
