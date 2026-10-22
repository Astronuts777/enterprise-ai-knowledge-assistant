"""Safe local storage and deterministic first-pass document parsing."""

import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from docx import Document as DocxDocument
from docx.table import Table
from pypdf import PdfReader

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md"}
MAX_PDF_PAGES = 100
MAX_OCR_PAGES = 10


class DocumentProcessingError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ParsedChunk:
    locator: str
    text: str


def safe_filename(filename: str | None) -> str:
    cleaned = Path(filename or "document").name
    if not cleaned or cleaned in {".", ".."}:
        raise DocumentProcessingError("Invalid filename")
    return cleaned


def validate_extension(filename: str) -> str:
    extension = Path(filename).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise DocumentProcessingError("Supported formats: PDF, DOCX, TXT, and Markdown")
    return extension


def parse_document(content: bytes, extension: str) -> list[ParsedChunk]:
    try:
        if extension in {".txt", ".md"}:
            text = content.decode("utf-8-sig")
            sections = [(f"paragraph:{index}", value) for index, value in _paragraphs(text)]
        elif extension == ".pdf":
            reader = PdfReader(BytesIO(content))
            if len(reader.pages) > MAX_PDF_PAGES:
                raise DocumentProcessingError("PDF 超过 100 页，请拆分后上传。")
            sections = []
            ocr_count = 0
            deadline = time.monotonic() + 90
            for index, page in enumerate(reader.pages, start=1):
                extracted = page.extract_text(extraction_mode="layout") or ""
                # Image-bearing pages may contain only a header or an incomplete text layer.
                if page.images:
                    ocr_count += 1
                    if ocr_count > MAX_OCR_PAGES:
                        raise DocumentProcessingError("图片页面超过 10 页，请拆分后上传进行 OCR。")
                    extracted = _ocr_page(content, index, deadline)
                sections.append((f"page:{index}", extracted))
        elif extension == ".docx":
            document = DocxDocument(BytesIO(content))
            sections = []
            paragraph_number = table_number = 0
            for block in document.iter_inner_content():
                if isinstance(block, Table):
                    table_number += 1
                    sections.extend(_table_sections(block, f"table:{table_number}"))
                else:
                    paragraph_number += 1
                    sections.append((f"paragraph:{paragraph_number}", block.text))
        else:
            raise DocumentProcessingError("Unsupported document format")
    except DocumentProcessingError:
        raise
    except Exception as error:
        raise DocumentProcessingError("The document could not be parsed") from error

    chunks = _chunk_sections(sections)
    if not chunks:
        raise DocumentProcessingError("未提取到可读正文。扫描件需要 OCR，请检查文件内容。")
    return chunks


def _table_sections(table: Table, locator: str) -> list[tuple[str, str]]:
    sections = []
    header = " | ".join(cell.text.strip() for cell in table.rows[0].cells) if table.rows else ""
    for index, row in enumerate(table.rows, start=1):
        values = " | ".join(cell.text.strip() for cell in row.cells)
        sections.append((f"{locator}:row:{index}", f"{header}\n{values}" if index > 1 else values))
        seen = set()
        for cell in row.cells:
            if cell._tc in seen:
                continue
            seen.add(cell._tc)
            for nested_index, nested in enumerate(cell.tables, start=1):
                sections.extend(_table_sections(
                    nested, f"{locator}:row:{index}:nested:{nested_index}",
                ))
    return sections


def _ocr_page(content: bytes, page_number: int, deadline: float) -> str:
    if not shutil.which("pdftoppm") or not shutil.which("tesseract"):
        raise DocumentProcessingError(
            f"第 {page_number} 页包含图片，当前服务器未安装 OCR，无法保证解析完整。"
        )
    try:
        # Per-request private files are removed on success, timeout and failure.
        with tempfile.TemporaryDirectory(prefix="enterprise-ocr-") as folder:
            source = Path(folder) / "source.pdf"
            source.write_bytes(content)
            os.chmod(source, 0o600)
            prefix = str(Path(folder) / "page")
            for command in (
                ["pdftoppm", "-f", str(page_number), "-l", str(page_number), "-singlefile",
                 "-scale-to", "2000", "-png", str(source), prefix],
                ["tesseract", f"{prefix}.png", "stdout", "-l", "eng+chi_sim", "--psm", "3"],
            ):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, 90)
                result = subprocess.run(
                    command, capture_output=True, check=True, timeout=min(30, remaining),
                    env={**os.environ, "OMP_THREAD_LIMIT": "1"},
                )
            value = result.stdout.decode("utf-8").strip()
    except (OSError, subprocess.SubprocessError, UnicodeError) as error:
        raise DocumentProcessingError(
            f"第 {page_number} 页 OCR 失败或超时，请拆分文件并检查扫描清晰度。"
        ) from error
    if not value:
        raise DocumentProcessingError(f"第 {page_number} 页 OCR 未识别到文字，请检查该页。")
    return value


def _paragraphs(text: str) -> list[tuple[int, str]]:
    values = [value.strip() for value in text.replace("\r\n", "\n").split("\n\n")]
    return [(index, value) for index, value in enumerate(values, start=1) if value]


def _chunk_sections(
    sections: list[tuple[str, str]], max_chars: int = 1200
) -> list[ParsedChunk]:
    chunks: list[ParsedChunk] = []
    for locator, raw_text in sections:
        normalized = "\n".join(" ".join(line.split()) for line in raw_text.splitlines()).strip()
        while normalized:
            split_at = min(len(normalized), max_chars)
            if split_at < len(normalized):
                natural_break = normalized.rfind(" ", 0, split_at)
                if natural_break >= max_chars // 2:
                    split_at = natural_break
            value = normalized[:split_at].strip()
            if value:
                chunks.append(ParsedChunk(locator=locator, text=value))
            normalized = normalized[split_at:].strip()
    return chunks
