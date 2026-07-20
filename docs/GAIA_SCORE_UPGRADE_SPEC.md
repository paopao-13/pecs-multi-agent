# PECS — GAIA 评分强化技术方案（自包含实施规格）

> 本文档可独立交给任何 AI/工程师执行。包含：根因诊断、四个杠杆的精确改动位置与代码、验证方式、重跑命令、诚实叙事口径。
> 项目：PECS 多智能体框架（Plan-Execute-Critic-Synthesize，LangGraph 编排）。目标：提升 GAIA 官方 Level 1（53 题）实测准确率，从 26.4% 推向 40%+。

---

## 0. 背景与诚实基线（必须先读）

当前 GAIA 官方 53 题实测（升级前）：

| 指标 | ReAct 基线 | PECS 多智能体 | 差值 | 统计检验 |
|------|:---:|:---:|:---:|:---:|
| 准确率（总体） | 24.5% (13/53) | 26.4% (14/53) | +1.9pp | McNemar p=1.0（不显著） |
| 准确率（无附件） | 28.6% (12/42) | 33.3% (14/42) | +4.8pp | - |
| 准确率（有附件） | 9.1% (1/11) | 0% (0/11) | -9.1pp | 附件题 PECS 均失败 |

**关键事实（决定方案可行性）：**
1. **公平基准是 ReAct 24.5%**，不是另一个弱基线（15.1%）。任何对外表述都必须用 24.5%，不得用 +11.3pp 的说法（那是拿弱基线比出来的，不诚实）。
2. **附件题 0% 是 harness 主动 hard-skip，不是模型天花板**。原 `benchmarks/gaia_official.py` 对 png/jpg/mp3/mp4 等扩展名直接 `continue` 判 0，根本没送进模型；而 `tools/file_parser.py` 已支持 PDF/Excel/CSV/图片 base64，能力是现成的，只是没接进 GAIA 流程。
3. **非附件错题（28 道）主因是 Web 检索落地失败**（论文/文章/视频/具体事实找不到）+ 少量数值算错。
4. WebShop 真实环境 +25pp（PECS 25% vs ReAct 0%）是**真实硬赢**，本次不动它。

**目标：** 通过四个技术杠杆实打实提分，把"绝对分低"转成"相对增益显著 + 边界清晰"，且**绝不篡改已跑出的诚实数字**——新数字必须来自真实重跑。

---

## 1. 四个杠杆总览（按 ROI）

| 杠杆 | 改动文件 | 成本 | 预计增益 | 是否需外部 Key |
|------|---------|------|---------|:---:|
| **L1 多模态解锁** | `tools/multimodal.py`(新) + `benchmarks/gaia_official.py` + `tools/__init__.py` | 中 | 附件子集 0%→可解（+4~10pp 总体） | 是（视觉模型，可免费档） |
| **L2 附件 PDF/CSV 接线** | `agents/executor.py` + `benchmarks/gaia_official.py` | 零 | +1~3pp | 否 |
| **L3 Web 检索增强** | `tools/web_search.py` + `agents/planner.py` | 中（可选 API） | +3~6pp | 否（DDG 默认）/ 可选 Tavily |
| **L4 放弃即重试** | `agents/synthesizer.py` | 零 | +1~3pp | 否 |

> **已实施状态：** 以下四个杠杆的代码**已全部落地**（由本仓库当前工作区提供），编译与 mock 单测均通过。本文档同时作为"给另一 AI 复刻/审查"的规格。
> **未做：** 真实重跑官方 53 题（需本机配多模态后端 + API Key + HuggingFace datasets，沙箱代不了）。新数字以重跑结果为准。

---

## 2. L1 — 多模态解锁（新增 `tools/multimodal.py`）

### 2.1 目的
把原 harness 对图片/音频/视频附件的 `continue`（判 0）改为：调用多模态后端预处理为文本后注入问题；**未配置后端或调用失败时优雅降级回 skip**，保证零回归。

### 2.2 新增文件 `tools/multimodal.py`（完整内容）

```python
"""
多模态附件处理工具

将 GAIA 中的图片 / 音频 / 视频附件转换为文本，供纯文本 LLM（如 deepseek-chat）使用。

后端：OpenAI 兼容协议（vision chat + audio transcription）。
配置（环境变量，均可选；未配置或调用失败时优雅降级，由 benchmarks/gaia_official.py 识别后跳过该附件）：
- PEC_VISION_BASE_URL : 视觉/转写端点基址，如 https://api.openai.com/v1
- PEC_VISION_MODEL    : 视觉模型名，如 gpt-4o-mini / 任意兼容视觉模型
- PEC_VISION_API_KEY  : 对应 API Key
- PEC_TRANSCRIBE_MODEL: 音频转写模型名（默认同 PEC_VISION_MODEL；部分端点支持 audio transcription）

设计原则：
- 绝不因缺少多模态后端或调用异常而让上层评测崩溃。
- 不可用时返回以 "[多模态处理不可用]" 开头的字符串；调用失败返回 "[多模态处理失败]"，
  均由调用方识别后优雅降级（保持与原 skip 行为一致，不破坏现有跑分）。
- 支持免费视觉模型（任意 OpenAI 兼容端点）或本地方案（音频转写需端点支持，视频需 ffmpeg/openh264）。
"""
import os

VISION_BASE_URL = os.getenv("PEC_VISION_BASE_URL", "")
VISION_MODEL = os.getenv("PEC_VISION_MODEL", "")
VISION_API_KEY = os.getenv("PEC_VISION_API_KEY", "")
TRANSCRIBE_MODEL = os.getenv("PEC_TRANSCRIBE_MODEL", VISION_MODEL)

_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")
_AUDIO_EXTS = (".mp3", ".m4a", ".wav", ".ogg", ".flac")
_VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".webm")


def _get_client():
    """构造 OpenAI 兼容客户端；未配置或导入失败返回 None。"""
    if not (VISION_BASE_URL and VISION_API_KEY):
        return None
    try:
        from openai import OpenAI
        return OpenAI(base_url=VISION_BASE_URL, api_key=VISION_API_KEY)
    except Exception:
        return None


def multimodal_process(args: dict) -> str:
    """
    将多模态附件转为文本。

    参数: args = {"path": "附件路径"}
    返回: 提取的文本；不可用时返回以 "[多模态处理不可用]" 开头的字符串，
          调用失败时返回 "[多模态处理失败]"。
    """
    path = args.get("path", "")
    if not path or not os.path.exists(path):
        return f"[多模态处理不可用] 文件不存在或路径为空: {path}"
    client = _get_client()
    if client is None:
        return "[多模态处理不可用] 未配置 PEC_VISION_BASE_URL / PEC_VISION_API_KEY，无法处理多模态附件。"
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in _IMAGE_EXTS:
            return _describe_image(client, path)
        if ext in _AUDIO_EXTS:
            return _transcribe_audio(client, path)
        if ext in _VIDEO_EXTS:
            return _handle_video(client, path)
    except Exception as e:
        return f"[多模态处理失败] {type(e).__name__}: {str(e)[:200]}"
    return f"[多模态处理不可用] 不支持的附件类型: {ext}"


def _describe_image(client, path: str) -> str:
    """用视觉模型描述图片内容（文字/数字/图表转录）。"""
    import base64
    mime = "image/png" if path.lower().endswith(".png") else "image/jpeg"
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    resp = client.chat.completions.create(
        model=VISION_MODEL,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": (
                    "请详细、准确地描述这张图片中的所有可读信息：文字、数字、公式、图表、表格内容。"
                    "如果图片包含题目或数据，请完整转录，不要遗漏任何细节。"
                )},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ],
        }],
        max_tokens=1500,
    )
    return resp.choices[0].message.content or ""


def _transcribe_audio(client, path: str) -> str:
    """用音频转写端点把音频转为文本。"""
    try:
        with open(path, "rb") as f:
            resp = client.audio.transcriptions.create(model=TRANSCRIBE_MODEL, file=f)
        return f"[音频转写文本]\n{resp.text or ''}"
    except Exception as e:
        return f"[多模态处理不可用] 音频转写不可用（端点不支持 audio transcription）: {type(e).__name__}"


def _handle_video(client, path: str) -> str:
    """视频：优先抽音频转写；无 ffmpeg 则抽帧描述；均不可用时降级。"""
    audio_path = _extract_audio(path)
    if audio_path:
        try:
            with open(audio_path, "rb") as f:
                resp = client.audio.transcriptions.create(model=TRANSCRIBE_MODEL, file=f)
            return f"[视频音频转写文本]\n{resp.text or ''}"
        except Exception:
            pass
        finally:
            if os.path.exists(audio_path):
                try:
                    os.remove(audio_path)
                except Exception:
                    pass
    frames = _extract_frames(path)
    if frames:
        descriptions = []
        for fr in frames:
            try:
                descriptions.append(_describe_image(client, fr))
            finally:
                if os.path.exists(fr):
                    try:
                        os.remove(fr)
                    except Exception:
                        pass
        if descriptions:
            return "[视频关键帧描述]\n" + "\n---\n".join(descriptions)
    return "[多模态处理不可用] 视频处理需要 ffmpeg（抽取音频）或 opencv（抽帧），当前环境均未提供。"


def _extract_audio(path: str):
    """用 ffmpeg 抽取音频为 mp3；无 ffmpeg 返回 None。"""
    import shutil
    import subprocess
    import tempfile
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    out = tempfile.mktemp(suffix=".mp3")
    try:
        subprocess.run(
            [ffmpeg, "-y", "-i", path, "-vn", out],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120,
        )
        return out if os.path.exists(out) and os.path.getsize(out) > 0 else None
    except Exception:
        return None


def _extract_frames(path: str):
    """用 opencv 抽 3 帧（10%/50%/90%）；无 opencv 返回空列表。"""
    try:
        import cv2
        import tempfile
    except Exception:
        return []
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        return []
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    indices = [int(total * r) for r in (0.1, 0.5, 0.9) if total > 0]
    out_paths = []
    for idx in indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ret, frame = cap.read()
        if ret:
            p = tempfile.mktemp(suffix=".png")
            cv2.imwrite(p, frame)
            out_paths.append(p)
    cap.release()
    return out_paths
```

### 2.3 注册进 `tools/__init__.py`

在 `TOOL_REGISTRY` 增加 `"multimodal": multimodal_process`，在 `TOOL_DESCRIPTIONS` 增加对应描述，并在文件顶部 import：

```python
from tools.multimodal import multimodal_process

TOOL_REGISTRY = {
    ...,
    "file_parse": file_parser,
    "multimodal": multimodal_process,   # 新增
    "api_call": api_caller,
    ...
}

TOOL_DESCRIPTIONS = {
    ...,
    "multimodal": "多模态附件处理工具。输入图片/音频/视频路径，返回识别出的文本（需配置PEC_VISION_*后端）。适用于GAIA多模态附件任务。",
}
```

### 2.4 改 `benchmarks/gaia_official.py`（替换原 hard-skip 块）

原代码（约 408–428 行）对图片/音视频直接 `continue` 判 0。替换为：

```python
        # 构造完整问题（含附件处理）
        full_question = question
        attachment_path = ds.resolve_attachment(sample)
        if attachment_path:
            ext = os.path.splitext(attachment_path)[1].lower()
            # 多模态附件（图片/音视频）：尝试预处理为文本后注入
            # 需配置多模态后端（PEC_VISION_*），未配置或失败则优雅降级跳过（保持原行为，不破坏现有跑分）
            if ext in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp",
                       ".mp3", ".m4a", ".wav", ".ogg", ".flac",
                       ".mp4", ".mov", ".mkv", ".webm"):
                try:
                    from tools.multimodal import multimodal_process
                    processed = multimodal_process({"path": attachment_path})
                except Exception as e:
                    processed = f"[多模态处理不可用] 导入/调用失败: {type(e).__name__}"
                if processed.startswith("[多模态处理不可用]") or processed.startswith("[多模态处理失败]"):
                    print(f"[{i+1}/{len(samples)}] {task_id}: 跳过（{ext} 多模态后端不可用：{processed[:60]}）")
                    results.append({
                        "task_id": task_id,
                        "question": question[:100],
                        "ground_truth": ground_truth,
                        "predicted": "",
                        "correct": False,
                        "tokens_used": 0,
                        "has_attachment": has_attachment,
                        "error": f"multimodal_skip_{ext}",
                    })
                    has_file_total += 1
                    continue
                full_question = (
                    f"{question}\n\n"
                    f"[附件（{ext}）已由多模态后端识别为文本]:\n{processed[:6000]}\n"
                    f"⚠️ 请基于以上附件内容作答。"
                )
            else:
                # 文本类附件（PDF/Excel/CSV/纯文本）：提示先用 file_parse 工具解析
                full_question = (
                    f"{question}\n\n"
                    f"[附件文件路径: {attachment_path}]\n"
                    f"⚠️ 本题附带文件，请务必先用 file_parse 工具读取并分析该文件内容，再结合问题作答。"
                )
```

> 注意：`has_file_total += 1` 只在多模态 skip 分支里加（与原逻辑一致）；文本类附件分支 fall through 到下方统一计数，不要重复加。

---

## 3. L2 — 附件 PDF/CSV 接线（零成本）

### 3.1 根因
`tools/file_parser.py` 与 `TOOL_REGISTRY`/`TOOL_DESCRIPTIONS` 已有 `file_parse`，`agents/planner.py` 也允许 `file_parse` 步骤。但 **`agents/executor.py` 的 `EXECUTOR_SYSTEM_PROMPT` 只列了 `file_read`，漏了 `file_parse`**——Executor 收到 file_parse 步骤时不知道参数格式，等于废了。

### 3.2 改 `agents/executor.py` 的 `EXECUTOR_SYSTEM_PROMPT`
在 `- file_read工具的args需要: {"path": "文件路径"}` 之后加一行：

```text
- file_parse工具的args需要: {"path": "文件路径"}（自动识别PDF/Excel/CSV/图片并提取内容；处理附件时优先用此工具而非 file_read）
- multimodal工具的args需要: {"path": "多模态附件路径"}（将图片/音频/视频转为文本，仅当多模态后端已配置时可用）
```

（harness 侧对文本类附件加"先 file_parse"提示已在 L1 的 §2.4 else 分支完成。）

---

## 4. L3 — Web 检索增强

### 4.1 改 `tools/web_search.py`：可选 Tavily 真实 API

在文件顶部加 env 读取，新增 `_tavily_search`，并在 `web_search()` 中 mock 未命中后、DDG 之前调用：

```python
import os
SEARCH_PROVIDER = os.getenv("PEC_SEARCH_PROVIDER", "").lower()
SEARCH_API_KEY = os.getenv("PEC_SEARCH_API_KEY", "")
```

`web_search()` 主体（mock 命中判断保持不变），在 `if not mock_result.startswith("[模拟搜索] 未找到"):` 之后插入：

```python
    # 配置了真实搜索 API（如 Tavily）时优先使用，获得更可靠的接地摘要
    if SEARCH_PROVIDER == "tavily" and SEARCH_API_KEY:
        try:
            result = _tavily_search(query, num_results)
            if result:
                return result
        except Exception:
            pass
```

新增函数：

```python
def _tavily_search(query: str, num_results: int) -> str:
    """使用 Tavily Search API 进行真实、接地（grounded）的网页搜索。"""
    url = "https://api.tavily.com/search"
    payload = json.dumps({
        "api_key": SEARCH_API_KEY,
        "query": query,
        "max_results": num_results,
        "search_depth": "advanced",
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    results = data.get("results", [])
    if not results:
        return ""
    snippets = []
    for r in results:
        title = r.get("title", "")
        content = r.get("content", "")
        url_r = r.get("url", "")
        if content:
            snippets.append(f"[搜索] {title}\n{content}\n来源: {url_r}")
    return "\n---\n".join(snippets) if snippets else ""
```

（未配置 `PEC_SEARCH_PROVIDER` 时完全走原 DDG/mock 路径，零回归。）

### 4.2 改 `agents/planner.py`：多跳 + 实算规则

在 `PLANNER_SYSTEM_PROMPT` 的「规则：」小节增加：

```text
- 涉及数值/数量/比例/距离/时间等计算的问题，必须包含 python 步骤进行实际计算，不要凭记忆估算答案
- 需要查找具体事实/文献/网页（如论文、文章、视频、具体数据）时：先 search 找到来源 URL，再用 web_browse 打开该 URL 提取精确值，不要仅凭搜索摘要作答
- 如果任务附带文件或图片，优先用 file_parse / multimodal 工具提取内容后再作答
```

并在同 prompt 的工具列表中加入 `multimodal` 一行（见 L1 §2.3 同款），以及把 action 白名单那行改为包含 `multimodal`：

```text
- action必须是: search / web_browse / python / file_read / file_parse / multimodal / api_call / webshop 之一
```

代码里 `allowed_actions` 集合同步加 `"multimodal"`。

---

## 5. L4 — 放弃即重试（零成本）

### 5.1 改 `agents/synthesizer.py` 的 `_should_reflect`

在「答案缺少明确结论」判断块之后、函数 `return False` 之前，增加放弃型答案检测：

```python
    # 放弃型答案：模型输出"无法确定/无法回答"等，而非真实结论
    # 此类答案几乎必错，应触发一次重规划，尝试不同工具组合（如 web_browse 打开具体网页、python 实算）
    giveup_kws = ["无法确定", "无法回答", "无法生成", "不能回答", "无法找到",
                  "找不到相关信息", "insufficient", "cannot determine", "unable to", "i cannot"]
    if any(kw in answer for kw in giveup_kws):
        return True

    return False
```

### 5.2 改 `_generate_reflection`：给放弃型更具体的提示

在「分析哪些步骤失败了」块之后追加：

```python
    # 放弃型答案：明确提示换工具 / 实际计算
    giveup_kws = ["无法确定", "无法回答", "无法生成", "不能回答", "无法找到", "找不到相关信息"]
    if any(kw in answer for kw in giveup_kws):
        reflection_parts.append(
            "上一次未能得出确定答案。请尝试不同策略：用 web_browse 打开具体来源网页提取精确值；"
            "涉及数值/数量时用 python 实际计算而非凭记忆；必要时将任务拆得更细、分步验证。"
        )
```

> 说明：`_should_reflect` 顶部已有的 `if complexity == "simple": return False` 与迭代上限保护仍然生效，不会无限循环。

---

## 6. 验证清单（改动后必须全过）

```bash
# 1) 编译所有改动文件
python -m py_compile agents/executor.py agents/planner.py agents/synthesizer.py \
    tools/__init__.py tools/multimodal.py tools/web_search.py benchmarks/gaia_official.py

# 2) mock 单测（不烧 Key、不联网）
python - <<'PY'
from tools.multimodal import multimodal_process
import tempfile, os
print(multimodal_process({"path": "D:/nope.png"})[:40])          # 期望: [多模态处理不可用] 文件不存在
with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
    f.write(b"fake"); tmp = f.name
print(multimodal_process({"path": tmp})[:60])                    # 期望: [多模态处理不可用] 未配置 PEC_VISION_*
os.remove(tmp)
from tools import TOOL_REGISTRY
assert "multimodal" in TOOL_REGISTRY
import agents.synthesizer as S
assert S._should_reflect("x", "根据所有执行步骤的结果，**无法确定**该信息。", [], 0, "complex") is True
from tools.web_search import web_search
assert web_search({"query": "zzz_unknown"})  # 不崩即可
print("ALL_OK")
PY

# 3) 模块导入冒烟（不触发 API）
python -c "import tools, tools.multimodal, tools.web_search, agents.planner, agents.executor, agents.synthesizer, agents.critic; print('IMPORT_OK')"
```

---

## 7. 重跑拿新数字（本机执行，沙箱代不了）

```bash
# .env 增加多模态后端（任意 OpenAI 兼容视觉端点；免费档即可，如 gpt-4o-mini）
PEC_VISION_BASE_URL=https://api.openai.com/v1
PEC_VISION_MODEL=gpt-4o-mini
PEC_VISION_API_KEY=sk-xxx
# 可选：真实搜索
PEC_SEARCH_PROVIDER=tavily
PEC_SEARCH_API_KEY=xxx

# 重跑官方 GAIA 53 题
python run_gaia_official.py
```

重跑后预期：
- 4 道多模态题（2 png + 2 mp3）从 0% 推向可解，文本类附件也回收一部分；
- 总体准确率有望从 26.4% 抬到 **40%+**，对 ReAct 24.5% 形成**真实且可能显著**的领先；
- WebShop +25pp 不受影响（真实硬赢）。

---

## 8. 诚实叙事口径（对外/简历/README 必须遵守）

1. **GAIA 对比一律用 ReAct 24.5% 作基准**，表述为 "PECS 26.4% vs ReAct 24.5%（+1.9pp，McNemar 不显著）"；**绝不可**写 "+11.3pp" 或 "GAIA benchmark 92%/100%"。
2. 若重跑后数字提升，**必须注明是真实重跑结果**，并保留升级前数字对照；不得把"预期增益"当"已达成"。
3. WebShop 真实环境 +25pp（3/12 vs 0/12）是真实硬赢，可放心讲。
4. 已知边界要主动披露：升级前附件子集 0%（根因是 harness 未接线，非模型）；升级后需配置多模态后端方可解锁——这是 senior 级的诚实信号。
5. 小样本声明保留：GAIA 官方 n=53 已做 McNemar；若仍不显著，如实写"方向性增益"，不夸大。

---

## 9. 文件改动清单（供 code review）

| 文件 | 改动 |
|------|------|
| `tools/multimodal.py` | **新增**：多模态附件→文本（图/音/视频），OpenAI 兼容后端，优雅降级 |
| `tools/__init__.py` | 注册 `multimodal` 到 TOOL_REGISTRY / TOOL_DESCRIPTIONS |
| `benchmarks/gaia_official.py` | 附件块：hard-skip → 多模态预处理注入 + 文本类附件提示 file_parse |
| `agents/executor.py` | EXECUTOR_SYSTEM_PROMPT 补 `file_parse`/`multimodal` 工具说明（L2） |
| `agents/planner.py` | 工具列表加 `multimodal`；加多跳/实算/附件规则；allowed_actions 加 `multimodal`（L3+L1） |
| `agents/synthesizer.py` | `_should_reflect` 加放弃型检测；`_generate_reflection` 加换工具提示（L4） |
| `tools/web_search.py` | 可选 Tavily 真实搜索 API（L3） |
| `README.md` | 特性加"多模态与附件处理"；评测表附件行标注；新增"能力升级·待重跑确认"块 |
| `.env.example` | 补 `PEC_VISION_*` / `PEC_SEARCH_*` 说明 |
