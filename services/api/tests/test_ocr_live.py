"""Real OCR regression, opt-in because external local executables are required."""

import os
import subprocess
from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject

from enterprise_ai_api.document_processing import parse_document


@pytest.mark.skipif(os.getenv("RUN_LOCAL_OCR_TESTS") != "1", reason="Requires local OCR tools")
def test_scanned_pdf_real_ocr(tmp_path: Path):
    writer = PdfWriter()
    page = writer.add_blank_page(width=600, height=200)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                             NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)}),
    })
    content = DecodedStreamObject()
    content.set_data(
        b"BT /F1 22 Tf 40 120 Td (Hotel reimbursement limit: AUD 180 per night.) Tj ET"
    )
    page[NameObject("/Contents")] = writer._add_object(content)
    original = tmp_path / "text.pdf"
    writer.write(original)
    assert "AUD 180" in parse_document(original.read_bytes(), ".pdf")[0].text

    prefix = tmp_path / "scan"
    subprocess.run(["pdftoppm", "-singlefile", "-scale-to", "1600", str(original), str(prefix)],
                   check=True, timeout=30, capture_output=True)
    magic, dimensions, maximum, pixels = prefix.with_suffix(".ppm").read_bytes().split(b"\n", 3)
    assert magic == b"P6" and maximum == b"255"
    width, height = map(int, dimensions.split())
    scanned = PdfWriter()
    scanned_page = scanned.add_blank_page(width=600, height=200)
    raster = DecodedStreamObject()
    raster.set_data(pixels)
    raster.update({NameObject("/Type"): NameObject("/XObject"),
                   NameObject("/Subtype"): NameObject("/Image"),
                   NameObject("/Width"): NumberObject(width),
                   NameObject("/Height"): NumberObject(height),
                   NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
                   NameObject("/BitsPerComponent"): NumberObject(8)})
    scanned_page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/XObject"): DictionaryObject({NameObject("/Im0"): scanned._add_object(raster)}),
    })
    drawing = DecodedStreamObject()
    drawing.set_data(b"q 600 0 0 200 0 0 cm /Im0 Do Q")
    scanned_page[NameObject("/Contents")] = scanned._add_object(drawing)
    output = tmp_path / "scanned.pdf"
    scanned.write(output)
    result = parse_document(output.read_bytes(), ".pdf")
    assert result[0].locator == "page:1"
    assert "AUD 180 per night" in result[0].text
    print({"real_ocr_text": result[0].text}, flush=True)
