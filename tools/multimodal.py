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
# 图片转录的输出上限。1500 会把整页截图的转录截断在中途（实测 GAIA 9318445f 的
# Wikipedia 算术页只转到 Fractions 章节就停），而题目要的数据常在页面更深处。
# 实测 glm-5.2-vision 单次视觉调用约 50~80s，加大 token 不影响时延量级。
VISION_MAX_TOKENS = int(os.getenv("PEC_VISION_MAX_TOKENS", "3000"))
# 大图分块阈值：超过则切块转录（实测视觉模型对大图只"看到"一部分，finish=stop 仍截断）
_TILE_MAX_W = int(os.getenv("PEC_VISION_TILE_MAX_W", "1400"))
_TILE_MAX_H = int(os.getenv("PEC_VISION_TILE_MAX_H", "1100"))
# 网关图片解码失败时视觉模型回复中的占位标记（小写匹配；中英都收）
_VISION_FAIL_MARKERS = (
    "图片内容描述失败", "图片内容未能成功",
    "image content description failed", "image description failed",
    "didn't come through", "no image data", "unable to see the image",
    "didn't see any actual image", "image didn't load",
)

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
    """用视觉模型描述图片内容（文字/数字/图表转录）。

    大图分块：实测视觉模型对大图（宽 >约1400px）只"看到"上半/左半部分——
    finish_reason=stop、远未到 max_tokens 就宣称「content cuts off here」
    （GAIA 9318445f 的 1726×842 截图转录到中部即止，8000 token 上限也一样）。
    故超过阈值时切成带重叠的分块逐块转录再合并。阈值可经
    PEC_VISION_TILE_MAX_W / PEC_VISION_TILE_MAX_H 调整（设极大值可关闭分块）。
    """
    import os
    try:
        from PIL import Image
        with Image.open(path) as im:
            w, h = im.size
    except Exception:
        w, h = 0, 0  # 无法探测尺寸时退化为单图描述

    if w <= _TILE_MAX_W and h <= _TILE_MAX_H:
        return _describe_single(client, path)

    cols = 2 if w > _TILE_MAX_W else 1
    rows = 2 if h > _TILE_MAX_H else 1
    try:
        from PIL import Image
        im = Image.open(path).convert("RGB")
    except Exception as e:
        return f"[多模态处理失败] 打开图片失败: {type(e).__name__}: {str(e)[:120]}"

    tw, th = im.size
    ox, oy = int(tw / cols * 0.12), int(th / rows * 0.12)  # 12% 重叠防切断文字
    parts = []
    for r in range(rows):
        for c in range(cols):
            left = max(0, c * tw // cols - (ox if c else 0))
            top = max(0, r * th // rows - (oy if r else 0))
            right = min(tw, (c + 1) * tw // cols + (ox if c < cols - 1 else 0))
            bottom = min(th, (r + 1) * th // rows + (oy if r < rows - 1 else 0))
            tile = im.crop((left, top, right, bottom))
            import tempfile
            fd, tmp = tempfile.mkstemp(suffix=".jpg")
            os.close(fd)
            try:
                tile.save(tmp, format="JPEG", quality=88)
                desc = _describe_single(client, tmp)
            finally:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            if desc.startswith("[多模态处理失败]"):
                return desc  # 任一块解码失败即整体失败，宁缺毋假
            parts.append(f"[图片分块 {r+1}/{rows} 行, {c+1}/{cols} 列]\n{desc}")
    return "\n\n".join(parts)


def _describe_single(client, path: str) -> str:
    """单张（或单块）图片的一次视觉转录。"""
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
        max_tokens=VISION_MAX_TOKENS,
    )
    content = resp.choices[0].message.content or ""
    # 网关侧图片解码失败时，视觉模型实际收到的是占位文本而非图片，回复形如
    #「…[图片内容描述失败]…请重新上传…」（中文）或「the image didn't come through /
    #  no image data」（英文，实测换英文提问时就是这版措辞）。这种「假成功」必须
    # 显式化为失败串，让评测侧按 multimodal_skip 记录，而不是把客套话当附件描述
    # 注入题面（实测：GAIA cca530fc 棋盘图在该网关 3 个 vision 模型 + PNG/JPEG
    # 重编码均如此；9318445f 的右侧分块也复现）。
    low = content.lower()
    if any(m in low for m in _VISION_FAIL_MARKERS):
        return "[多模态处理失败] 视觉后端未读取到图片数据（网关侧图片解码失败）"
    return content


def _transcribe_audio(client, path: str) -> str:
    """用音频转写端点把音频转为文本。"""
    try:
        with open(path, "rb") as f:
            resp = client.audio.transcriptions.create(model=TRANSCRIBE_MODEL, file=f)
        return f"[音频转写文本]\n{resp.text or ''}"
    except Exception as e:
        # 端点不支持 audio transcription，降级为可识别的不可用串
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
    # 抽帧描述
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
