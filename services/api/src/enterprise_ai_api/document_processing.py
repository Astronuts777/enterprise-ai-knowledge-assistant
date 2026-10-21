"""Safe local storage and deterministic first-pass document parsing."""

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from docx import Document as DocxDocument
from pypdf import PdfReader

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md"}


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
            sections = [
                (f"page:{index}", page.extract_text() or "")
                for index, page in enumerate(reader.pages, start=1)
            ]
        elif extension == ".docx":
            document = DocxDocument(BytesIO(content))
            sections = [
                (f"paragraph:{index}", paragraph.text)
                for index, paragraph in enumerate(document.paragraphs, start=1)
            ]
        else:
            raise DocumentProcessingError("Unsupported document format")
    except DocumentProcessingError:
        raise
    except Exception as error:
        raise DocumentProcessingError("The document could not be parsed") from error

    chunks = _chunk_sections(sections)
    if not chunks:
        raise DocumentProcessingError("No readable text was found in the document")
    return chunks


def _paragraphs(text: str) -> list[tuple[int, str]]:
    values = [value.strip() for value in text.replace("\r\n", "\n").split("\n\n")]
    return [(index, value) for index, value in enumerate(values, start=1) if value]


def _chunk_sections(
    sections: list[tuple[str, str]], max_chars: int = 1200
) -> list[ParsedChunk]:
    chunks: list[ParsedChunk] = []
    for locator, raw_text in sections:
        normalized = " ".join(raw_text.split())
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
