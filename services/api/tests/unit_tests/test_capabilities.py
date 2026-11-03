"""Parsing and read-only skill backend protections."""

import io
import socket
import zipfile

import pytest

from app.errors import APIError
from app.knowledge import lexical_rank, parse_document, split_sections
from app.mcp_service import validate_target
from app.models import ModelInput
from app.skill_library import SkillBackend, parse_skill

SKILL = b"---\nname: test-skill\ndescription: Test skill\n---\n\n# Instructions\nRead the reference.\n"


def test_chinese_keyword_retrieval_and_source_chunking() -> None:
    sections = parse_document("价格.md", "产品价格为 200 元。售后保修三年。".encode())
    chunks = split_sections(sections)
    assert chunks[0][0] == "全文"
    rows = [{"id": "a", "content": chunks[0][1]}, {"id": "b", "content": "天气和旅行"}]
    assert lexical_rank("产品价格", rows)[0][0] == "a"
    assert lexical_rank("量子卫星", rows) == []


def test_skill_import_and_virtual_backend_never_exposes_host_files() -> None:
    name, description, files = parse_skill("SKILL.md", SKILL)
    assert name == "test-skill" and description == "Test skill"
    backend = SkillBackend({f"/skills/{name}/{path}": text for path, text in files.items()})
    assert backend.download_files(["/skills/test-skill/SKILL.md"])[0].content == SKILL
    assert backend.download_files(["/etc/passwd"])[0].error
    with pytest.raises(PermissionError):
        backend.write("/skills/test-skill/SKILL.md", "modified")


def test_skill_zip_path_traversal_rejected() -> None:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("SKILL.md", SKILL)
        archive.writestr("../secret.txt", "bad")
    with pytest.raises(APIError):
        parse_skill("skill.zip", output.getvalue())


@pytest.mark.asyncio
async def test_invalid_mcp_address_returns_actionable_validation_error(monkeypatch) -> None:
    with pytest.raises(APIError) as invalid_port:
        await validate_target("http://127.0.0.1:invalid/mcp", True)
    assert invalid_port.value.status == 422

    def unavailable(*args):
        raise socket.gaierror("not found")

    monkeypatch.setattr(socket, "getaddrinfo", unavailable)
    with pytest.raises(APIError) as invalid_host:
        await validate_target("https://missing.example/mcp", False)
    assert invalid_host.value.status == 422


def test_embedding_model_cannot_replace_default_chat_model() -> None:
    with pytest.raises(ValueError, match="只有聊天模型"):
        ModelInput(
            name="vectors", model="embedding", base_url="http://localhost:8791/v1", kind="embedding", is_default=True
        )


def test_xlsx_does_not_execute_formulas_and_rejects_entity_expansion():
    from openpyxl import Workbook

    from app.spreadsheet_text import spreadsheet_sections

    book = Workbook()
    book.active.append(["=1+1"])
    content = io.BytesIO()
    book.save(content)
    book.close()
    sections = spreadsheet_sections(content.getvalue())
    assert "公式（未执行）：=1+1" in sections[0][1]
    unsafe = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(content.getvalue())) as original, zipfile.ZipFile(unsafe, "w") as output:
        for entry in original.infolist():
            value = original.read(entry.filename)
            if entry.filename == "xl/workbook.xml":
                value = b'<!DOCTYPE foo [<!ENTITY x "unsafe">]>' + value.replace(
                    b"<workbook ", b'<workbook data="&x;" ', 1
                )
            output.writestr(entry.filename, value)
    with pytest.raises(APIError):
        spreadsheet_sections(unsafe.getvalue())
