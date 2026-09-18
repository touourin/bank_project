from pathlib import PurePath

from bank_project.adapters.parsing.documents import check_archive, read_docx, read_pdf
from bank_project.adapters.parsing.tabular import TableParser, decode_text
from bank_project.contracts.errors import IntakeError, LimitExceeded
from bank_project.contracts.intake import ParsedSource, RawInput, TextBlock
from bank_project.contracts.models import ImportRequest
from bank_project.contracts.schema import IntakeLimits

SUPPORTED = {".csv", ".xlsx", ".json", ".jsonl", ".txt", ".md", ".pdf", ".docx"}


class FileParser:
    def __init__(self, limits: IntakeLimits):
        self.tables = TableParser(limits)
        self.limits = limits

    def parse(self, source: RawInput, request: ImportRequest) -> ParsedSource:
        filename = source.filename
        if not filename or len(filename) > 255 or any(c in filename for c in "/\\\x00"):
            raise IntakeError("文件名无效")
        extension = PurePath(filename).suffix.lower()
        if extension not in SUPPORTED:
            raise IntakeError("支持 CSV、XLSX、JSON、JSONL、TXT、MD、PDF、DOCX")
        if len(source.content) > self.limits.file_bytes:
            raise LimitExceeded("原始文件大小超限")
        if not source.content:
            raise IntakeError("不能导入空文件")
        try:
            return self._parse(source, request, extension)
        except IntakeError:
            raise
        except Exception as exc:
            raise IntakeError("文件解析失败，请检查格式、编码及文件是否损坏") from exc

    def _parse(self, source: RawInput, request: ImportRequest, extension: str) -> ParsedSource:
        content, filename = source.content, source.filename
        if request.sheet is not None and extension != ".xlsx":
            raise IntakeError("sheet 仅适用于 Excel 文件")
        if extension in {".xlsx", ".docx"}:
            check_archive(content, self.limits)
        if extension == ".csv":
            return self.tables.csv(content, filename, request.table)
        if extension == ".xlsx":
            return self.tables.xlsx(content, filename, request.table, request.sheet)
        if extension in {".json", ".jsonl"}:
            return self.tables.json(content, filename, request.table, extension == ".jsonl")
        if request.table is not None:
            raise IntakeError("文档不能指定业务表 table")
        warnings = []
        if extension in {".txt", ".md"}:
            blocks = [TextBlock(locator="text", text=decode_text(content))]
        elif extension == ".docx":
            blocks = read_docx(content)
        else:
            blocks, warnings = read_pdf(content, self.limits)
        if not blocks or not any(b.text.strip() for b in blocks):
            raise IntakeError("文档没有可提取的文本")
        if sum(len(b.text) for b in blocks) > self.limits.text_characters:
            raise LimitExceeded("文档文本量超限，请拆分")
        return ParsedSource(filename=filename, blocks=blocks, warnings=warnings)
