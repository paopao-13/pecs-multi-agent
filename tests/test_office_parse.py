"""Office 附件（.docx / .pptx）解析 + 数据目录白名单 单元测试

背景（为什么要这批用例）：
1. `tools/file_parser.py` 原先只分发 pdf/xlsx/csv/image，`.docx`/`.pptx` 落到
   `_parse_text` 回退 → 直接吐出 ZIP 二进制（`PK\\x03\\x04...`），是**静默错误**：
   工具返回 success，但内容全是乱码，LLM 拿它什么也答不出来。
2. `tools/path_guard.py` 黑名单含 `C:\\Users`，而 Windows 上用户数据（含
   HuggingFace 默认缓存 `~\\.cache`）默认就在 `C:\\Users` 下，还常带 "." 前缀目录
   命中「隐藏文件」规则。实测后果：GAIA 官方 53 题的 11 道附件题**全部**被拒解析，
   附件子集准确率被静默打成 0%。

因此既要有「OOXML 能正确解析」的用例，也要有「白名单能显式豁免」的用例，
并且**单测本身就用白名单机制**把 tmp_path 放行（Windows 下 tmp 在 C:\\Users）。

用例不依赖任何下载数据：全部用标准库 zipfile 现场构造最小合法 OOXML。
"""
import os
import zipfile

import pytest

from tools.file_parser import file_parser

_NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_NS_P = "http://schemas.openxmlformats.org/presentationml/2006/main"


@pytest.fixture(autouse=True)
def _allow_tmp_dir(tmp_path, monkeypatch):
    """Windows 下 pytest 的 tmp 目录位于 C:\\Users\\...，会被路径守卫拦下。

    这里正好把「数据目录白名单」用作放行手段 —— 既让用例跨平台可跑，
    也顺带验证了白名单本身生效（若白名单失效，下面所有用例都会失败）。
    """
    monkeypatch.setenv("PEC_DATA_ALLOW_DIR", str(tmp_path))


def _write_docx(path: str, paragraphs, table_cells=None) -> None:
    """构造最小合法 .docx：段落为 <w:p>/<w:t>，可选表格（文字同样包在 <w:p> 里）。"""
    body = "".join(f'<w:p><w:r><w:t>{t}</w:t></w:r></w:p>' for t in paragraphs)
    if table_cells:
        rows = "".join(
            f'<w:tr><w:tc><w:p><w:r><w:t>{c}</w:t></w:r></w:p></w:tc></w:tr>'
            for c in table_cells
        )
        body += f"<w:tbl>{rows}</w:tbl>"

    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{_NS_W}"><w:body>{body}</w:body></w:document>'
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", xml)


def _write_pptx(path: str, slide_texts) -> None:
    """构造最小合法 .pptx：slideN.xml 里 <a:p>/<a:t> 承载文本。"""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types/>')
        for idx, texts in enumerate(slide_texts, 1):
            paras = "".join(f'<a:p><a:r><a:t>{t}</a:t></a:r></a:p>' for t in texts)
            xml = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<p:sld xmlns:a="{_NS_A}" xmlns:p="{_NS_P}"><p:cSld><p:spTree>'
                f'<p:sp><p:txBody>{paras}</p:txBody></p:sp>'
                "</p:spTree></p:cSld></p:sld>"
            )
            z.writestr(f"ppt/slides/slide{idx}.xml", xml)


def test_docx_paragraphs_extracted(tmp_path):
    """.docx 正文段落应被提取，且不再回退成 ZIP 二进制"""
    p = tmp_path / "demo.docx"
    _write_docx(str(p), ["第一段内容", "第二段内容"])

    out = file_parser({"path": str(p)})

    assert "错误" not in out
    assert "第一段内容" in out and "第二段内容" in out
    assert "Word文件" in out
    # 关键回归点：修复前会吐出 ZIP 文件头
    assert "PK" not in out[:20]
    assert "\x03\x04" not in out


def test_docx_table_text_extracted(tmp_path):
    """.docx 表格内的文字也应被提取（表格文本同样包在 <w:p> 内）"""
    p = tmp_path / "with_table.docx"
    _write_docx(str(p), ["正文"], table_cells=["单元格A", "单元格B"])

    out = file_parser({"path": str(p)})

    assert "错误" not in out
    assert "单元格A" in out and "单元格B" in out


def test_pptx_slides_natural_order(tmp_path):
    """.pptx 多页顺序必须是自然序（slide10 不能排在 slide2 前面）"""
    p = tmp_path / "deck.pptx"
    _write_pptx(str(p), [["第1页"], ["第2页"], ["第10页"]])

    out = file_parser({"path": str(p)})

    assert "错误" not in out
    assert "共3页" in out
    assert out.index("第1页") < out.index("第2页") < out.index("第10页")
    assert "PPT文件" in out


def test_office_bad_zip_reports_error(tmp_path):
    """伪装成 .docx 的非 ZIP 文件必须报错，不能回退成乱码"""
    p = tmp_path / "fake.docx"
    p.write_text("not a zip at all")

    out = file_parser({"path": str(p)})

    assert out.startswith("错误")
    assert "docx" in out


def test_legacy_binary_office_rejected(tmp_path):
    """旧版二进制 .doc/.ppt 无法用 zipfile 解析，应明确报错而非静默乱码"""
    p = tmp_path / "legacy.doc"
    p.write_bytes(b"\xd0\xcf\x11\xe0")  # OLE 复合文档魔数

    out = file_parser({"path": str(p)})

    assert out.startswith("错误")
    assert "docx" in out or "转换" in out


def test_allowlist_unblocks_and_default_unchanged(tmp_path, monkeypatch):
    """白名单：配置后放行；不配置时系统敏感路径仍被拦截（默认行为不变）"""
    from tools.path_guard import is_forbidden_path

    inside = tmp_path / "a.docx"
    inside.write_text("x")
    assert is_forbidden_path(str(inside)) == ""  # 白名单内放行

    monkeypatch.delenv("PEC_DATA_ALLOW_DIR", raising=False)
    assert is_forbidden_path("/etc/passwd") == "敏感路径"
    assert is_forbidden_path("C:\\Windows\\System32\\config\\SAM") == "敏感路径"
