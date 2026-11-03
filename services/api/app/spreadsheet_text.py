"""Read bounded Excel cells as evidence without executing formulas or external links."""

import io
import zipfile
from contextlib import closing
from xml.etree.ElementTree import ParseError

from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import fromstring
from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from app.errors import fail


def spreadsheet_sections(data: bytes) -> list[tuple[str, str]]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            files = archive.infolist()
            if len(files) > 1000 or sum(item.file_size for item in files) > 20 * 1024 * 1024:
                fail(413, "DOCUMENT_TOO_LARGE", "Excel 展开大小超过 20 MB 或文件项超过 1000")
            for item in files:
                if item.filename.endswith(".xml"):
                    fromstring(archive.read(item), forbid_dtd=True)

        with closing(load_workbook(io.BytesIO(data), read_only=True, data_only=False, keep_links=False)) as workbook:
            if len(workbook.worksheets) > 30:
                fail(413, "DOCUMENT_TOO_LARGE", "Excel 最多包含 30 张工作表")
            sections, characters, cells = [], 0, 0
            for sheet in workbook.worksheets:
                if (sheet.max_row or 0) > 5000 or (sheet.max_column or 0) > 100:
                    fail(413, "DOCUMENT_TOO_LARGE", "每张工作表最多 5000 行、100 列")
                for index, row in enumerate(sheet.iter_rows(), 1):
                    cells += len(row)
                    if index > 5000 or len(row) > 100 or cells > 50000:
                        fail(413, "DOCUMENT_TOO_LARGE", "Excel 总单元格数最多 5 万")
                    values = [
                        cell.coordinate + ": " + ("公式（未执行）：" if cell.data_type == "f" else "") + str(cell.value)
                        for cell in row
                        if cell.value is not None
                    ]
                    if values:
                        text = " | ".join(values)
                        characters += len(text)
                        if characters > 200000:
                            fail(413, "DOCUMENT_TOO_LARGE", "Excel 提取文字超过 20 万字")
                        sections.append((f"工作表 {sheet.title} · 第 {index} 行", text))
            return sections
    except (KeyError, ValueError, TypeError, ParseError, DefusedXmlException, InvalidFileException, zipfile.BadZipFile):
        fail(422, "PARSE_FAILED", "Excel 文件格式不正确或包含禁止的 XML 内容")
