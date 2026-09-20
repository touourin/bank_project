# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License; adapted from GraphRAG Search (see LICENSE).

"""Validate uploads and turn each document into plain text for GraphRAG."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any
from xml.etree import ElementTree

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_BATCH_BYTES = 100 * 1024 * 1024
MAX_DOCUMENTS = 5000
MAX_EXTRACTED_BYTES = 100 * 1024 * 1024
SUPPORTED_EXTENSIONS = ("txt", "md", "pdf", "docx", "csv", "json", "jsonl")
_WORD = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class UploadValidationError(ValueError):
    """An upload cannot be imported without losing or misreading its contents."""


@dataclass(frozen=True)
class Document:
    """One source document, before dataset-level duplicate removal."""

    title: str
    text: str
    source_name: str

    @property
    def sha256(self) -> str:
        """Return a stable digest of this document's UTF-8 text."""
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


def _clean_text(value: str, *, strip: bool = True) -> str:
    if any(ord(char) < 32 and char not in "\t\n\r\f" for char in value):
        raise UploadValidationError("正文含有二进制或不支持的控制字符，请上传可读文本。")
    if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise UploadValidationError("正文含有无效的 Unicode 字符，请重新导出为 UTF-8 文本。")
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    return value.strip() if strip else value


def _decode(data: bytes, *, allow_gb18030: bool = False) -> str:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        if not allow_gb18030:
            raise UploadValidationError(
                "无法按 UTF-8 读取，请将文件转为 UTF-8 编码后重试。"
            ) from exc
        try:
            text = data.decode("gb18030")
        except UnicodeDecodeError as gb_error:
            raise UploadValidationError(
                "无法按 UTF-8 或 GB18030 读取 TXT，请检查文件编码。"
            ) from gb_error
    return _clean_text(text, strip=False)


def _document(title: str, text: str, name: str) -> Document:
    text = _clean_text(text)
    title = _clean_text(title)
    if not text:
        raise UploadValidationError("正文为空，请补充内容后重试。")
    if len(text.encode("utf-8")) > MAX_EXTRACTED_BYTES:
        raise UploadValidationError("提取后的正文超过 100 MB，请拆分文件。")
    return Document(title=title.strip() or PurePath(name).stem, text=text, source_name=name)


def _record_document(record: Any, name: str, index: int, label: str) -> Document:
    if not isinstance(record, dict):
        raise UploadValidationError(f"{label}必须是包含 text（或 content）字段的对象。")
    field = "text" if "text" in record else "content"
    if field not in record or not isinstance(record[field], str):
        raise UploadValidationError(f"{label}缺少文本类型的 text（或 content）字段。")
    title = record.get("title")
    if title is not None and not isinstance(title, str):
        raise UploadValidationError(f"{label}的 title 字段必须是文本。")
    try:
        return _document(title or f"{PurePath(name).stem} · {index}", record[field], name)
    except UploadValidationError as exc:
        raise UploadValidationError(f"{label}：{exc}") from exc


def _csv_documents(data: bytes, name: str) -> list[Document]:
    csv.field_size_limit(max(csv.field_size_limit(), MAX_FILE_BYTES))
    reader = csv.DictReader(io.StringIO(_decode(data), newline=""), strict=True)
    try:
        headers = reader.fieldnames
        if not headers:
            raise UploadValidationError("CSV 文件为空，需包含 text（或 content）列。")
        headers = [header.strip() for header in headers]
        if any(not header for header in headers) or len(set(headers)) != len(headers):
            raise UploadValidationError("CSV 列名不能为空或重复。")
        if not {"text", "content"}.intersection(headers):
            raise UploadValidationError("CSV 需包含 text（或 content）列，可选 title 列。")
        reader.fieldnames = headers
        documents = []
        for index, row in enumerate(reader, 1):
            label = f"CSV 第 {reader.line_num} 行"
            if None in row or any(value is None for value in row.values()):
                raise UploadValidationError(f"{label}的字段数量与表头不一致。")
            documents.append(_record_document(row, name, index, label))
            _check_document_count(len(documents))
        if not documents:
            raise UploadValidationError("CSV 只有表头，没有文档数据。")
        return documents
    except csv.Error as exc:
        raise UploadValidationError(f"CSV 第 {reader.line_num} 行格式错误：{exc}") from exc


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"不支持的 JSON 常量 {value}")


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    record = {}
    for key, value in pairs:
        if key in record:
            raise ValueError(f"JSON 字段重复：{key}")
        record[key] = value
    return record


def _json_loads(text: str, label: str) -> Any:
    try:
        return json.loads(
            text,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_unique_json_object,
        )
    except (ValueError, RecursionError) as exc:
        raise UploadValidationError(f"{label}格式错误：{exc}") from exc


def _json_documents(data: bytes, name: str, *, lines: bool) -> list[Document]:
    text = _decode(data)
    documents = []
    if lines:
        for line_number, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            label = f"JSONL 第 {line_number} 行"
            record = _json_loads(line, label)
            documents.append(_record_document(record, name, len(documents) + 1, label))
            _check_document_count(len(documents))
    else:
        records = _json_loads(text, "JSON")
        if isinstance(records, dict):
            records = [records]
        if not isinstance(records, list):
            raise UploadValidationError("JSON 顶层必须是文档对象或文档对象数组。")
        _check_document_count(len(records))
        for index, record in enumerate(records, 1):
            documents.append(_record_document(record, name, index, f"JSON 第 {index} 条记录"))
    if not documents:
        raise UploadValidationError("文件没有文档数据。")
    return documents


def _docx_blocks(element: ElementTree.Element) -> list[str]:
    if element.tag == f"{_WORD}p":
        fragments = []
        for node in element.iter():
            if node.tag == f"{_WORD}t":
                fragments.append(node.text or "")
            elif node.tag == f"{_WORD}tab":
                fragments.append("\t")
            elif node.tag in {f"{_WORD}br", f"{_WORD}cr"}:
                fragments.append("\n")
        return ["".join(fragments)]
    if element.tag == f"{_WORD}tbl":
        return [
            "\t".join("\n".join(_docx_blocks(cell)) for cell in row.findall(f"{_WORD}tc"))
            for row in element.findall(f"{_WORD}tr")
        ]
    return [block for child in element for block in _docx_blocks(child)]


def _docx_document(data: bytes, name: str) -> Document:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if (
                len(entries) > 10000
                or sum(item.file_size for item in entries) > MAX_EXTRACTED_BYTES
            ):
                raise UploadValidationError("DOCX 解压后超过限制，请缩减文件或拆分文档。")
            document_entries = [item for item in entries if item.filename == "word/document.xml"]
            if len(document_entries) != 1:
                raise UploadValidationError("不是有效的 DOCX 文件，缺少唯一的正文。")
            with archive.open(document_entries[0]) as document_file:
                xml = document_file.read(MAX_EXTRACTED_BYTES + 1)
            if len(xml) > MAX_EXTRACTED_BYTES:
                raise UploadValidationError("DOCX 正文超过 100 MB，请拆分文档。")
        if re.search(rb"<!\s*(?:DOCTYPE|ENTITY)", xml.replace(b"\x00", b""), re.IGNORECASE):
            raise UploadValidationError("DOCX 包含不支持的 XML 声明，请用 Word 重新另存为 DOCX。")
        root = ElementTree.fromstring(xml)
        body = root.find(f"{_WORD}body")
        if body is None:
            raise UploadValidationError("DOCX 缺少正文，请重新另存为 DOCX。")
        text = "\n\n".join(block for block in _docx_blocks(body) if block.strip())
    except UploadValidationError:
        raise
    except Exception as exc:
        raise UploadValidationError(
            "无法读取 DOCX，请确认文件未损坏、未加密，并重新另存为 DOCX。"
        ) from exc
    if not text.strip():
        raise UploadValidationError(
            "DOCX 没有可提取的正文；如果内容是图片，请先进行 OCR 文字识别。"
        )
    return _document(PurePath(name).stem, text, name)


def _pdf_document(data: bytes, name: str) -> Document:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise UploadValidationError("PDF 解析组件 pypdf 未安装，请安装应用依赖后重试。") from exc
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise UploadValidationError("PDF 已加密，请解密后重新上传。")
        if len(reader.pages) > 5000:
            raise UploadValidationError("PDF 超过 5000 页，请拆分文档。")
        pages = []
        extracted_size = 0
        for page in reader.pages:
            text = page.extract_text() or ""
            extracted_size += len(text.encode("utf-8"))
            if extracted_size > MAX_EXTRACTED_BYTES:
                raise UploadValidationError("PDF 提取后的正文超过 100 MB，请拆分文档。")
            pages.append(text)
    except UploadValidationError:
        raise
    except Exception as exc:
        raise UploadValidationError("无法读取 PDF，请确认文件未损坏且包含可复制的文字。") from exc
    text = "\n\n".join(pages)
    if not text.strip():
        raise UploadValidationError(
            "PDF 没有可提取的文字，可能是扫描件；请先进行 OCR 文字识别，再上传文字版 PDF 或 TXT。"
        )
    return _document(PurePath(name).stem, text, name)


def _check_document_count(count: int) -> None:
    if count > MAX_DOCUMENTS:
        raise UploadValidationError(f"一次最多导入 {MAX_DOCUMENTS} 篇文档，请分批上传。")


def parse_uploads(files: list[tuple[str, bytes]]) -> list[Document]:
    """Parse every file atomically; retain duplicates for the dataset service.

    Plain text, Markdown, PDF and DOCX each produce one document. Each CSV row,
    JSON object and nonempty JSONL line produces one document. Any invalid record
    aborts the whole batch instead of silently importing a partial dataset.
    """
    if not files:
        raise UploadValidationError("请至少上传一个文件。")
    if sum(len(data) for _, data in files) > MAX_BATCH_BYTES:
        raise UploadValidationError("本次上传总大小超过 100 MB，请分批上传。")
    documents: list[Document] = []
    extracted_bytes = 0
    for name, data in files:
        try:
            if (
                not name
                or name in {".", ".."}
                or any(char in name for char in "/\\")
                or any(ord(char) < 32 for char in name)
                or any(0xD800 <= ord(char) <= 0xDFFF for char in name)
            ):
                raise UploadValidationError("文件名不能包含路径、斜杠或空字符。")
            if len(data) > MAX_FILE_BYTES:
                raise UploadValidationError("单个文件不能超过 20 MB，请拆分后重试。")
            if not data:
                raise UploadValidationError("文件为空。")
            extension = PurePath(name).suffix.lower().lstrip(".")
            if extension not in SUPPORTED_EXTENSIONS:
                raise UploadValidationError(
                    "不支持此文件格式；支持 TXT、MD、PDF、DOCX、CSV、JSON 和 JSONL。"
                )
            if extension == "csv":
                parsed = _csv_documents(data, name)
            elif extension in {"json", "jsonl"}:
                parsed = _json_documents(data, name, lines=extension == "jsonl")
            elif extension == "docx":
                parsed = [_docx_document(data, name)]
            elif extension == "pdf":
                parsed = [_pdf_document(data, name)]
            else:
                parsed = [
                    _document(
                        PurePath(name).stem, _decode(data, allow_gb18030=extension == "txt"), name
                    )
                ]
            extracted_bytes += sum(len(document.text.encode("utf-8")) for document in parsed)
            if extracted_bytes > MAX_EXTRACTED_BYTES:
                raise UploadValidationError("本次提取后的正文总量超过 100 MB，请分批上传。")
            documents.extend(parsed)
            _check_document_count(len(documents))
        except UploadValidationError as exc:
            raise UploadValidationError(f"{name}：{exc}") from exc
    return documents
