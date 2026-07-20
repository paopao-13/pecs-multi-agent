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
