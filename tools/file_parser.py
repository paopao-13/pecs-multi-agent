"""
文件解析工具

扩展原 file_reader 的能力，支持 GAIA L1 常见的文件类型：
- PDF：提取文本内容（优先 PyMuPDF，回退 pdfminer.six）
- Excel：提取所有工作表为文本（openpyxl）
- CSV：读取并返回表格内容（内置 csv）
- Word（.docx）：解析 word/document.xml 的段落文本（**标准库 zipfile + ElementTree**）
- PowerPoint（.pptx）：按页解析 ppt/slides/slideN.xml 的文本（**标准库**）
- 图片：返回 base64 编码 + 提示用多模态模型分析
- 纯文本：直接返回内容（兼容原 file_reader）

Executor 在 Planner 规划出 file_parse 步骤时调用本工具。

关于 Office 文件：
.docx / .pptx 本质是 ZIP + XML（OOXML），用标准库即可解析，无需引入
python-docx / python-pptx，避免为两个格式背上一串传递依赖。
旧版二进制格式（.doc / .ppt，OLE 复合文档）无法用这种方式解析，明确报错。
"""
import os
import base64
import zipfile
import xml.etree.ElementTree as ET

from tools.path_guard import is_forbidden_path

# OOXML 命名空间
_NS_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"   # WordprocessingML
_NS_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"          # DrawingML


def file_parser(args: dict) -> str:
    """
    文件解析工具

    参数:
        args: {
            "path": "文件路径",
            "sheet": "Excel工作表名(可选)",
            "max_chars": "最大返回字符数(默认8000)"
        }

    返回:
        解析后的内容字符串
    """
    path = args.get("path", "")
    max_chars = int(args.get("max_chars", 8000))

    if not path:
        return "错误：缺少 path 参数"

    # 路径安全校验（共用守卫，跨平台生效，实现见 tools/path_guard.py）
    reason = is_forbidden_path(path)
    if reason in ("敏感路径", "隐藏文件"):
        return f"错误：禁止访问{reason}"
    if not os.path.exists(path):
        return f"错误：文件不存在 '{path}'"
    if not os.path.isfile(path):
        return f"错误：路径不是文件 '{path}'"
    if os.path.getsize(path) > 20 * 1024 * 1024:
        return f"错误：文件过大 ({os.path.getsize(path)} bytes)，最大支持 20MB"

    ext = os.path.splitext(path)[1].lower()

    try:
        if ext in (".pdf",):
            return _parse_pdf(path, max_chars)
        elif ext in (".xlsx", ".xlsm", ".xls"):
            return _parse_excel(path, args.get("sheet"), max_chars)
        elif ext in (".csv",):
            return _parse_csv(path, max_chars)
        elif ext in (".docx",):
            return _parse_docx(path, max_chars)
        elif ext in (".pptx",):
            return _parse_pptx(path, max_chars)
        elif ext in (".doc", ".ppt"):
            # 旧版 OLE 复合文档，无法用 zipfile 解析；明确报错而不是回退成乱码
            return f"错误：暂不支持旧版二进制格式 {ext}，请转换为 {ext}x 后再解析"
        elif ext in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"):
            return _parse_image(path, max_chars)
        else:
            # 纯文本回退
            return _parse_text(path, max_chars)
    except Exception as e:
        return f"错误：解析文件失败 [{ext}]: {type(e).__name__}: {str(e)[:200]}"


def _parse_text(path: str, max_chars: int) -> str:
    for enc in ["utf-8", "gbk", "latin-1", "utf-16"]:
        try:
            with open(path, "r", encoding=enc) as f:
                content = f.read()
            return f"[文本文件 {os.path.basename(path)}]\n{content[:max_chars]}"
        except UnicodeDecodeError:
            continue
    return f"错误：无法解码文本文件 '{path}'"


def _parse_pdf(path: str, max_chars: int) -> str:
    text = ""
    # 优先 PyMuPDF
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(path)
        parts = []
        for page in doc:
            parts.append(page.get_text())
        text = "\n".join(parts)
        doc.close()
        return f"[PDF文件 {os.path.basename(path)}，共{doc.page_count if False else len(parts)}页]\n{text[:max_chars]}"
    except ImportError:
        pass
    # 回退 pdfminer.six
    try:
        from pdfminer.high_level import extract_text
        text = extract_text(path)
        return f"[PDF文件 {os.path.basename(path)}]\n{text[:max_chars]}"
    except ImportError:
        return "错误：PDF解析需要安装 PyMuPDF 或 pdfminer.six（pip install pymupdf）"
    except Exception as e:
        return f"错误：PDF解析失败: {str(e)[:200]}"


def _parse_excel(path: str, sheet: str, max_chars: int) -> str:
    try:
        import openpyxl
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
        out = [f"[Excel文件 {os.path.basename(path)}，工作表: {', '.join(wb.sheetnames)}]"]
        sheets = [sheet] if sheet and sheet in wb.sheetnames else wb.sheetnames
        for name in sheets:
            ws = wb[name]
            out.append(f"\n--- 工作表: {name} ---")
            for i, row in enumerate(ws.iter_rows(values_only=True)):
                if i >= 200:  # 最多200行
                    out.append("...(数据截断，仅显示前200行)")
                    break
                cells = ["" if c is None else str(c) for c in row]
                out.append(" | ".join(cells))
        wb.close()
        return "\n".join(out)[:max_chars]
    except ImportError:
        return "错误：Excel解析需要安装 openpyxl（pip install openpyxl）"
    except Exception as e:
        return f"错误：Excel解析失败: {str(e)[:200]}"


def _parse_csv(path: str, max_chars: int) -> str:
    import csv
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            rows = []
            for i, row in enumerate(reader):
                if i >= 200:
                    rows.append("...(数据截断，仅显示前200行)")
                    break
                rows.append(" | ".join("" if c is None else str(c) for c in row))
            header = rows[0] if rows else ""
            body = "\n".join(rows)
            return f"[CSV文件 {os.path.basename(path)}]\n列: {header}\n{body[:max_chars]}"
    except Exception as e:
        # 回退纯文本
        return _parse_text(path, max_chars)


def _parse_docx(path: str, max_chars: int) -> str:
    """解析 .docx（OOXML）—— 标准库 zipfile + ElementTree，零新增依赖。

    结构：.docx 是 ZIP 包，正文在 `word/document.xml`；
    段落为 `<w:p>`，文本节点为 `<w:t>`（`<w:tab/>` 制表符、`<w:br/>` 换行）。
    表格里的文字同样包在 `<w:p>` 内，因此按文档顺序遍历所有 `<w:p>`
    即可覆盖正文 + 表格，无需单独处理 `<w:tbl>`。
    """
    try:
        with zipfile.ZipFile(path) as z:
            if "word/document.xml" not in z.namelist():
                return f"错误：不是有效的 .docx 文件（缺少 word/document.xml）"
            xml_bytes = z.read("word/document.xml")
    except zipfile.BadZipFile:
        return f"错误：不是有效的 .docx 文件（ZIP 解析失败） '{os.path.basename(path)}'"

    root = ET.fromstring(xml_bytes)
    paras = []
    for p in root.iter(_NS_W + "p"):
        buf = []
        for node in p.iter():
            if node.tag == _NS_W + "t":
                buf.append(node.text or "")
            elif node.tag == _NS_W + "tab":
                buf.append("\t")
            elif node.tag in (_NS_W + "br", _NS_W + "cr"):
                buf.append("\n")
        line = "".join(buf).strip()
        if line:
            paras.append(line)

    text = "\n".join(paras)
    head = f"[Word文件 {os.path.basename(path)}，共{len(paras)}段]"
    return f"{head}\n{text[:max_chars]}"


def _parse_pptx(path: str, max_chars: int) -> str:
    """解析 .pptx（OOXML）—— 标准库 zipfile + ElementTree，零新增依赖。

    结构：幻灯片在 `ppt/slides/slideN.xml`（需按数字自然序排，不能用字符串序，
    否则 slide10 会排在 slide2 前面），文本节点为 DrawingML 的 `<a:t>`，
    段落为 `<a:p>`。按页分隔输出，便于 LLM 定位"第几页写了什么"。
    """
    import re

    try:
        with zipfile.ZipFile(path) as z:
            slides = [n for n in z.namelist()
                      if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)]
            if not slides:
                return "错误：不是有效的 .pptx 文件（未找到 ppt/slides/slideN.xml）"
            slides.sort(key=lambda n: int(re.search(r"(\d+)", os.path.basename(n)).group(1)))

            out = [f"[PPT文件 {os.path.basename(path)}，共{len(slides)}页]"]
            for idx, name in enumerate(slides, 1):
                root = ET.fromstring(z.read(name))
                lines = []
                for p in root.iter(_NS_A + "p"):
                    line = "".join(t.text or "" for t in p.iter(_NS_A + "t")).strip()
                    if line:
                        lines.append(line)
                if lines:
                    out.append(f"\n--- 第{idx}页 ---")
                    out.extend(lines)
            return "\n".join(out)[:max_chars]
    except zipfile.BadZipFile:
        return f"错误：不是有效的 .pptx 文件（ZIP 解析失败） '{os.path.basename(path)}'"


def _parse_image(path: str, max_chars: int) -> str:
    # 图片需要多模态模型，这里返回 base64 提示
    try:
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        return (f"[图片文件 {os.path.basename(path)}]\n"
                f"图片已编码为base64（长度 {len(b64)} 字符）。\n"
                f"请将此 base64 传给支持视觉的模型进行分析。\n"
                f"BASE64_START:{b64[:max_chars]}BASE64_END")
    except Exception as e:
        return f"错误：图片读取失败: {str(e)[:200]}"
