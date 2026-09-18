import io
import zipfile

from defusedxml import ElementTree
from pypdf import PdfReader

from bank_project.contracts.errors import IntakeError, LimitExceeded
from bank_project.contracts.intake import TextBlock
from bank_project.contracts.schema import IntakeLimits


def check_archive(content: bytes, limits: IntakeLimits) -> None:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        entries = archive.infolist()
        if len(entries) > 10000 or sum(i.file_size for i in entries) > limits.expanded_bytes:
            raise LimitExceeded("Office 文件解压大小或条目数超限")
        if len({i.filename for i in entries}) != len(entries):
            raise IntakeError("Office 文件含重复条目")
        for entry in entries:
            if entry.flag_bits & 1:
                raise IntakeError("不支持加密 Office 文件")
            if entry.file_size > max(entry.compress_size, 1) * 1000:
                raise LimitExceeded("Office 文件压缩率异常")


def read_docx(content: bytes) -> list[TextBlock]:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        root = ElementTree.fromstring(archive.read("word/document.xml"))
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    blocks = []
    for index, paragraph in enumerate(root.iter(f"{namespace}p"), 1):
        pieces = []
        for node in paragraph.iter():
            if node.tag == f"{namespace}t":
                pieces.append(node.text or "")
            elif node.tag == f"{namespace}tab":
                pieces.append("\t")
            elif node.tag in {f"{namespace}br", f"{namespace}cr"}:
                pieces.append("\n")
        text = "".join(pieces)
        if text.strip():
            blocks.append(TextBlock(locator=f"docx:paragraph:{index}", text=text))
    return blocks


def read_pdf(content: bytes, limits: IntakeLimits) -> tuple[list[TextBlock], list[str]]:
    reader = PdfReader(io.BytesIO(content), strict=True)
    if reader.is_encrypted:
        raise IntakeError("请先解密 PDF 后再导入")
    if len(reader.pages) > limits.pdf_pages:
        raise LimitExceeded("PDF 页数超限，请拆分")
    blocks = []
    blank_pages = []
    for number, page in enumerate(reader.pages, 1):
        # The library also caps decompression; reject excessive content streams before extraction.
        stream = page.get_contents()
        if stream and len(stream.get_data()) > limits.expanded_bytes:
            raise LimitExceeded("PDF 页面内容流过大")
        text = page.extract_text() or ""
        if text.strip():
            blocks.append(TextBlock(locator=f"pdf:page:{number}", text=text))
        else:
            blank_pages.append(number)
        if sum(len(block.text) for block in blocks) > limits.text_characters:
            raise LimitExceeded("文档文本量超限，请拆分")
    if blank_pages:
        # Partial extraction would hide scanned pages; require a complete searchable document.
        raise IntakeError("PDF 存在无法提取文本的页面，请完成 OCR 或移除确认无内容的空白页后重试")
    return blocks, []
