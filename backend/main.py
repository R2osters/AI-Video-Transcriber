from fastapi import Body, FastAPI, HTTPException, UploadFile, File, Form, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import os
import asyncio
import logging
import time
from pathlib import Path
from typing import Optional
import aiofiles
import uuid
import json
import re
import secrets
import shutil
import openai

from video_processor import VideoProcessor
from transcriber import Transcriber
from summarizer import Summarizer
from translator import Translator
from pipeline import (
    MEDIA_MIME,
    NO_SPEECH_NOTICE,
    UPLOAD_ALLOWED_EXT,
    VIDEO_EXT,
    media_kind as _media_kind,
    sanitize_title_for_filename as _sanitize_title_for_filename,
    transcribed_speech as _transcribed_speech,
    txt_to_raw_transcript_markdown as _txt_to_raw_transcript_markdown,
)
from diarizer import Diarizer
from library import LibraryStore

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="AI视频转录器", version="1.0.0")

# 本地访问令牌（桌面版由 Electron 在启动后端时注入 AVT_TOKEN）。
#
# 桌面版把后端监听在 127.0.0.1，但浏览器里的任意网页也能访问该地址：
# 若不加限制，用户随便打开一个页面就能读取整个转录库——包括长期保存的
# 原始音频。设置 AVT_TOKEN 后：
#   1) 不再输出 CORS 头，跨源读取被浏览器拦截；
#   2) /api 与 /ws 需携带令牌，令牌由服务端注入到 index.html，页面自带；
#   3) 校验 Host，阻断 DNS 重绑定。
# 未设置时行为与旧版完全一致，Web/Docker 部署不受影响。
AVT_TOKEN = os.getenv("AVT_TOKEN", "").strip()

app.add_middleware(
    CORSMiddleware,
    # 有令牌即桌面版：页面与接口同源，无需任何跨源许可
    allow_origins=[] if AVT_TOKEN else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_TOKEN_EXEMPT_PATHS = frozenset({"/api/health"})
_ALLOWED_HOSTS = ("127.0.0.1", "localhost", "[::1]")


@app.middleware("http")
async def _local_access_guard(request, call_next):
    """桌面版的本地访问校验；未设置 AVT_TOKEN 时直接放行。"""
    if not AVT_TOKEN:
        return await call_next(request)

    # Host 只允许回环地址，挫败 DNS 重绑定（域名解析到 127.0.0.1）
    host = (request.headers.get("host") or "").split(":")[0]
    if host and host not in _ALLOWED_HOSTS:
        return JSONResponse({"detail": "主机名不被允许"}, status_code=403)

    path = request.url.path
    if path.startswith(("/api", "/ws")) and path not in _TOKEN_EXEMPT_PATHS:
        supplied = (
            request.headers.get("x-avt-token")
            or request.query_params.get("token")
            or ""
        )
        if not secrets.compare_digest(supplied, AVT_TOKEN):
            return JSONResponse({"detail": "缺少或无效的本地访问令牌"}, status_code=401)

    return await call_next(request)

# 获取项目根目录
PROJECT_ROOT = Path(__file__).parent.parent

# 桌面版（PyInstaller/Electron）通过环境变量覆盖静态资源与数据目录
STATIC_DIR = Path(os.getenv("AVT_STATIC_DIR", str(PROJECT_ROOT / "static")))
DATA_ROOT = Path(os.getenv("AVT_DATA_DIR", str(PROJECT_ROOT)))

# 挂载静态文件
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# 创建临时目录
TEMP_DIR = DATA_ROOT / "temp"
TEMP_DIR.mkdir(parents=True, exist_ok=True)

# 用户可把转录库放到别的盘：系统盘往往先满，而这个库只增不减。
# 选择记在 DATA_ROOT/config.json（很小，始终留在默认位置）。
CONFIG_FILE = DATA_ROOT / "config.json"


def load_app_config() -> dict:
    try:
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f) or {}
    except Exception as e:
        logger.warning(f"读取配置失败，使用默认值: {e}")
    return {}


def save_app_config(cfg: dict) -> None:
    tmp = CONFIG_FILE.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(str(tmp), str(CONFIG_FILE))


def resolve_library_dir() -> Path:
    """配置指定的库目录；未配置或不可用时回落到默认位置。"""
    configured = (load_app_config().get("library_dir") or "").strip()
    if configured:
        path = Path(configured)
        try:
            path.mkdir(parents=True, exist_ok=True)
            return path
        except OSError as e:
            # 目标盘拔掉了也不能让程序起不来：回默认位置并说明原因
            logger.error(f"配置的转录库目录不可用（{configured}），回落到默认位置: {e}")
    return DATA_ROOT / "library"


# 转录库：任务完成后产物从 temp 迁入此处长期保存
LIBRARY_DIR = resolve_library_dir()
library_store = LibraryStore(LIBRARY_DIR)
try:
    # 磁盘为准：补录索引里缺失的记录，剔除目录已消失的行
    library_store.reindex()
except Exception as e:
    logger.warning(f"转录库索引重建失败（不影响转录）: {e}")

# 初始化处理器
video_processor = VideoProcessor()

# Whisper 模型：默认取环境变量，前端可按请求覆盖（缓存实例避免重复加载）
WHISPER_MODEL_DEFAULT = os.getenv("WHISPER_MODEL_SIZE", "base")
WHISPER_ALLOWED_MODELS = {
    "tiny", "tiny.en", "base", "base.en", "small", "small.en",
    "medium", "medium.en", "large-v1", "large-v2", "large-v3", "large",
    "turbo", "large-v3-turbo", "distil-large-v3",
}
import threading

transcriber = Transcriber(WHISPER_MODEL_DEFAULT)
_transcribers = {WHISPER_MODEL_DEFAULT: transcriber}
_transcribers_lock = threading.Lock()

def get_transcriber(model_size: str = "") -> Transcriber:
    """按请求返回对应模型的转录器；空值或非法值回退默认模型。"""
    size = (model_size or "").strip()
    if not size or size == WHISPER_MODEL_DEFAULT:
        return transcriber
    if size not in WHISPER_ALLOWED_MODELS:
        logger.warning(f"未知 Whisper 模型 '{size}'，回退默认 {WHISPER_MODEL_DEFAULT}")
        return transcriber
    with _transcribers_lock:
        if size not in _transcribers:
            _transcribers[size] = Transcriber(size)
        return _transcribers[size]

summarizer = Summarizer()
translator = Translator()
diarizer = Diarizer()

# 存储任务状态 - 使用文件持久化
import threading

TASKS_FILE = TEMP_DIR / "tasks.json"
tasks_lock = threading.Lock()

def load_tasks():
    """加载任务状态"""
    try:
        if TASKS_FILE.exists():
            with open(TASKS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except:
        pass
    return {}

def save_tasks(tasks_data):
    """保存任务状态"""
    try:
        with tasks_lock:
            with open(TASKS_FILE, 'w', encoding='utf-8') as f:
                json.dump(tasks_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存任务状态失败: {e}")

async def broadcast_task_update(task_id: str, task_data: dict):
    """向所有连接的SSE客户端广播任务状态更新"""
    logger.info(f"广播任务更新: {task_id}, 状态: {task_data.get('status')}, 连接数: {len(sse_connections.get(task_id, []))}")
    if task_id in sse_connections:
        connections_to_remove = []
        for queue in sse_connections[task_id]:
            try:
                await queue.put(json.dumps(task_data, ensure_ascii=False))
                logger.debug(f"消息已发送到队列: {task_id}")
            except Exception as e:
                logger.warning(f"发送消息到队列失败: {e}")
                connections_to_remove.append(queue)
        
        # 移除断开的连接
        for queue in connections_to_remove:
            sse_connections[task_id].remove(queue)
        
        # 如果没有连接了，清理该任务的连接列表
        if not sse_connections[task_id]:
            del sse_connections[task_id]

# 启动时加载任务状态
tasks = load_tasks()
# 存储正在处理的URL，防止重复处理
processing_urls = set()
# 存储活跃的任务对象，用于控制和取消
active_tasks = {}
# 存储SSE连接，用于实时推送状态更新
sse_connections = {}

# 本地上传：允许的类型与大小上限（MB），可用环境变量 UPLOAD_MAX_MB 调整
# 覆盖 pipeline 中的默认列表：支持更多格式（.aac/.opus/.wma/.aiff/.mov/.avi）
UPLOAD_ALLOWED_EXT = frozenset({
    ".txt",
    # audio
    ".mp3", ".m4a", ".wav", ".ogg", ".flac", ".aac", ".opus", ".wma", ".aiff",
    # vidéo
    ".mp4", ".webm", ".mkv", ".mov", ".avi",
})
UPLOAD_MAX_MB = int(os.getenv("UPLOAD_MAX_MB", "200"))

# 原视频下载：清晰度上限，避免 4K 源拖慢任务并占满磁盘
VIDEO_MAX_HEIGHT = int(os.getenv("VIDEO_MAX_HEIGHT", "720"))

def _resolve_temp_file(filename: str, allowed_ext: frozenset) -> Path:
    """校验前端传入的文件名并解析为 temp 目录下的真实路径。"""
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="文件名格式无效")

    ext = Path(filename).suffix.lower()
    if ext not in allowed_ext:
        raise HTTPException(status_code=400, detail=f"不支持的文件类型: {ext or '(none)'}")

    file_path = TEMP_DIR / filename
    # 二次防御：即使扩展名校验通过，也确认最终路径没跳出 temp
    if file_path.parent.resolve() != TEMP_DIR.resolve():
        raise HTTPException(status_code=400, detail="文件名格式无效")
    if file_path.exists():
        return file_path

    # temp 里没有 → 可能已迁入转录库（任务完成即搬走），按原文件名回落解析。
    # 路径不由文件名拼接，而是查表拿到记录后由库自身校验，故不会绕过上面的防御。
    located = library_store.find_by_filename(filename)
    if located:
        archived = library_store.asset_path(*located)
        if archived is not None:
            return archived

    raise HTTPException(status_code=404, detail="文件不存在")


def _safe_download_name(raw: str, expected_ext: str) -> Optional[str]:
    """把前端建议的下载文件名清洗为安全值，扩展名强制与磁盘文件一致。"""
    if not raw:
        return None
    base = os.path.basename(raw.replace("\\", "/"))
    stem = _sanitize_title_for_filename(Path(base).stem)
    if not stem or stem == "untitled":
        return None
    return f"{stem}{expected_ext}"


async def _resolve_media(
    task_id: str,
    safe_title: str,
    media_task: Optional[asyncio.Task],
    media_filename: Optional[str],
    media_download_name: Optional[str],
) -> tuple[Optional[str], Optional[str]]:
    """
    收尾等待并行进行的原视频下载，返回 (media_filename, media_download_name)。

    重复 await 同一个已完成的 Task 是安全的（直接返回缓存结果），因此慢速路径
    提前 await 过也不影响这里再次等待。
    """
    if media_task is None:
        return media_filename, media_download_name

    if not media_task.done():
        tasks[task_id].update({"progress": 92, "message": "正在准备原视频..."})
        save_tasks(tasks)
        await broadcast_task_update(task_id, tasks[task_id])

    try:
        video_path, _ = await media_task
    except Exception as e:
        logger.warning(f"等待原视频下载失败: {e}")
        video_path = None

    if video_path:
        return Path(video_path).name, f"{safe_title}{Path(video_path).suffix}"
    return media_filename, media_download_name


def _media_result_fields(
    media_filename: Optional[str],
    media_download_name: Optional[str],
) -> dict:
    """把媒体文件信息整理成写入任务结果的字段；文件不存在时返回空 dict。"""
    if not media_filename:
        return {}

    media_path = TEMP_DIR / media_filename
    if not media_path.exists():
        logger.warning(f"媒体文件不存在，跳过原视频展示: {media_filename}")
        return {}

    return {
        "media_filename": media_filename,
        "media_download_name": media_download_name or media_filename,
        "media_kind": _media_kind(media_path.suffix.lower()),
        # 传字节数，由前端决定用 KB/MB/GB 展示
        "media_size_bytes": media_path.stat().st_size,
    }


def _format_ts(seconds: float, sep: str) -> str:
    """秒 → HH:MM:SS,mmm（SRT）或 HH:MM:SS.mmm（VTT）。"""
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def _segments_to_srt(segments: list) -> str:
    lines = []
    for i, seg in enumerate(segments, 1):
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        speaker = seg.get("speaker")
        if speaker:
            text = f"{speaker}: {text}"
        lines.append(str(i))
        lines.append(f"{_format_ts(seg['start'], ',')} --> {_format_ts(seg['end'], ',')}")
        lines.append(text)
        lines.append("")
    return "\n".join(lines)


def _segments_to_vtt(segments: list) -> str:
    lines = ["WEBVTT", ""]
    for seg in segments:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        speaker = seg.get("speaker")
        if speaker:
            text = f"<v {speaker}>{text}"
        lines.append(f"{_format_ts(seg['start'], '.')} --> {_format_ts(seg['end'], '.')}")
        lines.append(text)
        lines.append("")
    return "\n".join(lines)


def _rebuild_transcript_with_speakers(raw_script: str, segments: list) -> str:
    """用带说话人标签的分段重建 Markdown 正文，保留原头部（语言信息）。"""
    marker = "## Transcription Content"
    idx = raw_script.find(marker)
    head = raw_script[: idx + len(marker)] if idx != -1 else raw_script
    lines = [head, ""]
    for seg in segments:
        start = transcriber._format_time(seg["start"])
        end = transcriber._format_time(seg["end"])
        speaker = seg.get("speaker")
        prefix = f"**{speaker}:** " if speaker else ""
        lines.append(f"**[{start} - {end}]**")
        lines.append("")
        lines.append(f"{prefix}{seg['text']}")
        lines.append("")
    return "\n".join(lines)


async def _maybe_diarize(task_id: str, audio_path: str, segments, raw_script: str):
    """启用时执行说话人分离；返回（可能更新的）segments 与 raw_script。"""
    if not diarizer.enabled or not segments:
        return segments, raw_script
    try:
        tasks[task_id].update({
            "progress": 48,
            "message": "正在识别说话人...",
        })
        save_tasks(tasks)
        await broadcast_task_update(task_id, tasks[task_id])

        turns = await diarizer.diarize(audio_path)
        if turns:
            segments = Diarizer.assign_speakers(segments, turns)
            raw_script = _rebuild_transcript_with_speakers(raw_script, segments)
            logger.info(f"说话人分离完成: {len(turns)} 个语音段")
    except Exception as e:
        logger.warning(f"说话人分离失败，继续无标签流程: {e}")
    return segments, raw_script


def _platform_from_url(url: str) -> str:
    """从来源 URL 粗略识别平台名，仅用于列表展示。"""
    if not url or not url.startswith("http"):
        return "upload"
    host = re.sub(r"^https?://(www\.)?", "", url).split("/")[0].lower()
    known = {
        "youtube.com": "youtube", "youtu.be": "youtube",
        "bilibili.com": "bilibili", "b23.tv": "bilibili",
        "vimeo.com": "vimeo", "twitter.com": "twitter", "x.com": "twitter",
        "tiktok.com": "tiktok", "douyin.com": "douyin",
    }
    for domain, name in known.items():
        if host == domain or host.endswith("." + domain):
            return name
    return host or "web"


def _archive_task_to_library(
    task_id: str,
    segments: Optional[list] = None,
    whisper_model: str = "",
) -> None:
    """任务完成后把产物从 temp 迁入转录库。

    产物是**移动**而非复制：temp 随时可能被清空，迁走后旧的
    /api/download/{filename}、/api/media/{filename} 链接靠库内的文件名映射继续解析。
    失败绝不能影响任务本身——用户已经拿到结果，归档只是持久化。
    """
    task = tasks.get(task_id)
    if not task:
        return

    def _temp(name: Optional[str]) -> Optional[Path]:
        if not name:
            return None
        path = TEMP_DIR / Path(str(name)).name
        return path if path.is_file() else None

    assets: dict = {}
    for kind, name in (
        ("srt", task.get("srt_filename")),
        ("vtt", task.get("vtt_filename")),
        ("raw", task.get("raw_script_file")),
        ("script", task.get("script_path")),
        ("summary", task.get("summary_path")),
        ("translation", task.get("translation_path")),
    ):
        path = _temp(name)
        if path:
            assets[kind] = path

    media_name = task.get("media_filename")
    media_path = _temp(media_name)
    if media_path:
        assets[_media_kind(media_path.suffix.lower())] = media_path

    created_at = task.get("created_at") or time.time()
    url = task.get("url") or ""
    entry = {
        "title": task.get("video_title") or "(untitled)",
        "created_at": created_at,
        "platform": _platform_from_url(url),
        "source_url": url if url.startswith("http") else "",
        "lang_src": task.get("detected_language") or "",
        "lang_dst": task.get("summary_language") or "",
        "model": task.get("whisper_model") or whisper_model or WHISPER_MODEL_DEFAULT,
        "source_mode": task.get("source_mode") or "",
        "elapsed_ms": int(max(0.0, time.time() - created_at) * 1000),
        "no_speech": bool(task.get("no_speech")),
        "script": task.get("script") or "",
        "summary": task.get("summary") or "",
        "translation": task.get("translation") or "",
        # 保留分段，日后可重新导出字幕而无需重跑 Whisper
        "segments": segments or [],
        "media_download_name": task.get("media_download_name") or "",
        # 媒体体积单独留存：条目总大小含文本，前端展示播放器信息时需要精确值
        "media_size_bytes": task.get("media_size_bytes") or 0,
    }

    try:
        item = library_store.add(entry, assets=assets, entry_id=task_id)
        tasks[task_id]["library_id"] = item.get("id") or task_id
        save_tasks(tasks)
    except Exception as e:
        logger.error(f"归档到转录库失败（结果仍可用）: {e}")


async def _run_post_extract_pipeline(
    task_id: str,
    raw_script: str,
    video_title: str,
    source_ref: str,
    summary_language: str,
    request_summarizer: Summarizer,
    dedup_url: Optional[str] = None,
    api_key: str = "",
    model_base_url: str = "",
    model_id: str = "",
    media_task: Optional[asyncio.Task] = None,
    media_filename: Optional[str] = None,
    media_download_name: Optional[str] = None,
    segments: Optional[list] = None,
) -> None:
    """取得 raw_script 后的共用管线：归档、优化、翻译、摘要、附带原视频、广播。

    media_task: 并行进行中的原视频下载任务，会在收尾前 await（可能已完成）。
    media_filename / media_download_name: 已就位的媒体文件（如本地上传的原件）。
    """
    short_id = task_id.replace("-", "")[:6]
    safe_title = _sanitize_title_for_filename(video_title)

    # 有 Whisper 分段时导出 SRT/VTT 字幕文件
    if segments:
        try:
            srt_filename = f"subtitles_{safe_title}_{short_id}.srt"
            vtt_filename = f"subtitles_{safe_title}_{short_id}.vtt"
            (TEMP_DIR / srt_filename).write_text(_segments_to_srt(segments), encoding="utf-8")
            (TEMP_DIR / vtt_filename).write_text(_segments_to_vtt(segments), encoding="utf-8")
            tasks[task_id].update({
                "srt_filename": srt_filename,
                "vtt_filename": vtt_filename,
            })
            save_tasks(tasks)
        except Exception as e:
            logger.error(f"生成SRT/VTT失败: {e}")

    try:
        raw_md_filename = f"raw_{safe_title}_{short_id}.md"
        raw_md_path = TEMP_DIR / raw_md_filename
        with open(raw_md_path, "w", encoding="utf-8") as f:
            f.write((raw_script or "") + f"\n\nsource: {source_ref}\n")
        tasks[task_id].update({"raw_script_file": raw_md_filename})
        save_tasks(tasks)
        await broadcast_task_update(task_id, tasks[task_id])
    except Exception as e:
        logger.error(f"保存原始转录Markdown失败: {e}")

    # ── 无语音短路 ────────────────────────────────────────────────────
    # 视频里没有人说话时，绝不能把空文稿交给 LLM：模型会编造出一整段与视频
    # 毫无关系的对话，再经翻译、摘要放大，用户完全无法分辨真假。
    if not _transcribed_speech(raw_script):
        logger.warning(f"任务 {task_id} 未检测到任何语音，跳过优化/翻译/摘要以避免 LLM 编造内容")

        media_filename, media_download_name = await _resolve_media(
            task_id, safe_title, media_task, media_filename, media_download_name
        )

        detected_language = (transcriber.get_detected_language(raw_script) or "").strip()
        notice = NO_SPEECH_NOTICE
        script_with_title = f"# {video_title}\n\n{notice}\n\nsource: {source_ref}\n"

        script_filename = f"transcript_{safe_title}_{short_id}.md"
        async with aiofiles.open(TEMP_DIR / script_filename, "w", encoding="utf-8") as f:
            await f.write(script_with_title)

        task_result = {
            "status": "completed",
            "progress": 100,
            "message": "未检测到语音内容",
            "no_speech": True,
            "video_title": video_title,
            "script": script_with_title,
            "summary": "",
            "script_path": str(TEMP_DIR / script_filename),
            "short_id": short_id,
            "safe_title": safe_title,
            "detected_language": detected_language,
            "summary_language": summary_language,
        }
        task_result.update(_media_result_fields(media_filename, media_download_name))

        tasks[task_id].update(task_result)
        save_tasks(tasks)
        # 广播前归档：先搬完文件，前端拿到结果时链接已能解析到库内
        await asyncio.to_thread(_archive_task_to_library, task_id, segments)
        await broadcast_task_update(task_id, tasks[task_id])

        if dedup_url:
            processing_urls.discard(dedup_url)
        if task_id in active_tasks:
            del active_tasks[task_id]
        return

    tasks[task_id].update({
        "progress": 55,
        "message": "正在优化转录文本...",
    })
    save_tasks(tasks)
    await broadcast_task_update(task_id, tasks[task_id])

    script = await request_summarizer.optimize_transcript(raw_script)

    script_with_title = f"# {video_title}\n\n{script}\n\nsource: {source_ref}\n"

    detected_language = transcriber.get_detected_language(raw_script)
    detected_language = (detected_language or "").strip()
    if not detected_language:
        detected_language = translator.infer_language_code(raw_script)
    detected_language = translator.normalize_lang_code(detected_language) or detected_language

    logger.info(f"检测到的语言: {detected_language}, 摘要语言: {summary_language}")

    translation_content = None
    translation_filename = None
    translation_path = None

    eff_key = (api_key or "").strip()
    eff_base = (model_base_url or "").strip().rstrip("/")
    if eff_key:
        request_translator = Translator(
            api_key=eff_key,
            base_url=eff_base or None,
            model=model_id or None,
        )
    else:
        request_translator = translator

    need_translation = translator.languages_differ_for_translation(
        detected_language, summary_language
    )

    if need_translation:
        logger.info(f"需要翻译: {detected_language} -> {summary_language}")
        tasks[task_id].update({
            "progress": 70,
            "message": "正在生成翻译...",
        })
        save_tasks(tasks)
        await broadcast_task_update(task_id, tasks[task_id])

        translation_content = await request_translator.translate_text(
            script, summary_language, detected_language
        )
        translation_with_title = f"# {video_title}\n\n{translation_content}\n\nsource: {source_ref}\n"
        translation_filename = f"translation_{safe_title}_{short_id}.md"
        translation_path = TEMP_DIR / translation_filename
        async with aiofiles.open(translation_path, "w", encoding="utf-8") as f:
            await f.write(translation_with_title)
    else:
        logger.info(
            f"不需要翻译: detected_language={detected_language}, summary_language={summary_language}, "
            f"need_translation={need_translation}"
        )

    tasks[task_id].update({
        "progress": 80,
        "message": "正在生成摘要...",
    })
    save_tasks(tasks)
    await broadcast_task_update(task_id, tasks[task_id])

    summary = await request_summarizer.summarize(script, summary_language, video_title)
    summary_with_source = summary + f"\n\nsource: {source_ref}\n"

    script_filename = f"transcript_{task_id}.md"
    script_path = TEMP_DIR / script_filename
    async with aiofiles.open(script_path, "w", encoding="utf-8") as f:
        await f.write(script_with_title)

    new_script_filename = f"transcript_{safe_title}_{short_id}.md"
    new_script_path = TEMP_DIR / new_script_filename
    try:
        if script_path.exists():
            script_path.rename(new_script_path)
            script_path = new_script_path
    except Exception:
        pass

    summary_filename = f"summary_{safe_title}_{short_id}.md"
    summary_path = TEMP_DIR / summary_filename
    async with aiofiles.open(summary_path, "w", encoding="utf-8") as f:
        await f.write(summary_with_source)

    # 下载与转录/摘要并行跑，到这里通常已经完成
    media_filename, media_download_name = await _resolve_media(
        task_id, safe_title, media_task, media_filename, media_download_name
    )

    task_result = {
        "status": "completed",
        "progress": 100,
        "message": "处理完成！",
        "video_title": video_title,
        "script": script_with_title,
        "summary": summary_with_source,
        "script_path": str(script_path),
        "summary_path": str(summary_path),
        "short_id": short_id,
        "safe_title": safe_title,
        "detected_language": detected_language,
        "summary_language": summary_language,
    }

    if translation_content and translation_path:
        task_result.update({
            "translation": translation_with_title,
            "translation_path": str(translation_path),
            "translation_filename": translation_filename,
        })

    task_result.update(_media_result_fields(media_filename, media_download_name))

    tasks[task_id].update(task_result)
    save_tasks(tasks)
    # 广播前归档：先搬完文件，前端拿到结果时链接已能解析到库内
    await asyncio.to_thread(_archive_task_to_library, task_id, segments)
    logger.info(f"任务完成，准备广播最终状态: {task_id}")
    await broadcast_task_update(task_id, tasks[task_id])
    logger.info(f"最终状态已广播: {task_id}")

    if dedup_url:
        processing_urls.discard(dedup_url)
    if task_id in active_tasks:
        del active_tasks[task_id]


@app.get("/")
async def read_root():
    """返回前端页面。

    桌面版会把本地访问令牌注入页面：接口与页面同源，前端因此无需任何配置，
    而浏览器里的第三方页面拿不到这个令牌。
    """
    index_path = STATIC_DIR / "index.html"
    if not AVT_TOKEN:
        return FileResponse(str(index_path))

    html = index_path.read_text(encoding="utf-8")
    injected = f'<script>window.__AVT_TOKEN__ = "{AVT_TOKEN}";</script>'
    html = html.replace("</head>", f"{injected}\n</head>", 1)
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


def _ws_token_ok(ws: WebSocket) -> bool:
    """WebSocket 不经过 HTTP 中间件，需单独校验令牌。"""
    if not AVT_TOKEN:
        return True
    return secrets.compare_digest(ws.query_params.get("token") or "", AVT_TOKEN)


@app.get("/api/health")
async def health_check():
    """健康检查（桌面版启动探测用）"""
    return {"status": "ok"}


@app.post("/api/models")
async def list_models(
    base_url: str = Form(default=""),
    api_key:  str = Form(default=""),
):
    """Proxy: fetch model list from any OpenAI-compatible API."""
    effective_key = api_key or os.getenv("OPENAI_API_KEY", "")
    effective_url = base_url.rstrip("/") or os.getenv("OPENAI_BASE_URL") or None

    if not effective_key:
        raise HTTPException(status_code=400, detail="API key is required")

    try:
        client = openai.OpenAI(api_key=effective_key, base_url=effective_url)
        resp   = await asyncio.to_thread(client.models.list)
        models = [{"id": m.id, "name": getattr(m, "name", m.id)} for m in resp.data]
        # Sort by id for readability
        models.sort(key=lambda x: x["id"])
        return {"data": models}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


async def _enqueue_upload_job(
    file: UploadFile,
    summary_language: str,
    api_key: str,
    model_base_url: str,
    model_id: str,
    whisper_model: str = "",
    audio_language: str = "",
) -> dict:
    """保存上传文件并入队 process_upload_task，返回 {task_id, message}。"""
    raw_name = file.filename or "upload.bin"
    if ".." in raw_name or "/" in raw_name or "\\" in raw_name:
        raise HTTPException(status_code=400, detail="Invalid filename")
    safe_name = os.path.basename(raw_name)
    ext = Path(safe_name).suffix.lower()
    if ext not in UPLOAD_ALLOWED_EXT:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {ext or '(none)'}",
        )

    max_bytes = UPLOAD_MAX_MB * 1024 * 1024
    task_id = str(uuid.uuid4())
    unique_stem = task_id.replace("-", "")[:12]
    dest = TEMP_DIR / f"upload_{unique_stem}{ext}"

    total = 0
    with open(dest, "wb") as out_f:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                try:
                    dest.unlink(missing_ok=True)
                except Exception:
                    pass
                raise HTTPException(
                    status_code=413,
                    detail=f"File exceeds limit of {UPLOAD_MAX_MB} MB",
                )
            out_f.write(chunk)

    if total == 0:
        try:
            dest.unlink(missing_ok=True)
        except Exception:
            pass
        raise HTTPException(status_code=400, detail="Empty file")

    video_title = _sanitize_title_for_filename(Path(safe_name).stem) or "upload"
    source_label = f"upload:{safe_name}"

    tasks[task_id] = {
        "status": "processing",
        "progress": 0,
        "message": "开始处理上传文件...",
        "script": None,
        "summary": None,
        "error": None,
        "url": source_label,
        "created_at": time.time(),
    }
    save_tasks(tasks)

    bg = asyncio.create_task(
        process_upload_task(
            task_id,
            dest,
            safe_name,
            video_title,
            ext,
            summary_language,
            api_key,
            model_base_url,
            model_id,
            whisper_model,
            audio_language,
        )
    )
    active_tasks[task_id] = bg

    return {"task_id": task_id, "message": "任务已创建，正在处理中..."}


def _sanitize_audio_language(code: str) -> str:
    """校验用户指定的音频语言代码；空或 auto 表示自动检测。"""
    code = (code or "").strip().lower()
    if not code or code == "auto":
        return ""
    return code if re.fullmatch(r"[a-z]{2,3}", code) else ""


@app.post("/api/process-video")
async def process_video(
    url: str = Form(default=""),
    summary_language: str = Form(default="zh"),
    api_key: str = Form(default=""),
    model_base_url: str = Form(default=""),
    model_id: str = Form(default=""),
    download_video: str = Form(default="1"),
    whisper_model: str = Form(default=""),
    audio_language: str = Form(default=""),
    file: Optional[UploadFile] = File(None),
):
    """
    处理视频链接或本地上传（multipart 中带 file 且无有效 URL 时走上传流程）。
    上传与 URL 共用此路径，便于反向代理只放行 /api/process-video 的环境。
    """
    want_video = str(download_video).strip().lower() not in ("0", "false", "no", "off", "")
    try:
        audio_language = _sanitize_audio_language(audio_language)

        if file is not None and (file.filename or "").strip():
            return await _enqueue_upload_job(
                file, summary_language, api_key, model_base_url, model_id,
                whisper_model, audio_language,
            )

        stripped = (url or "").strip()
        if not stripped:
            raise HTTPException(
                status_code=400,
                detail="Provide a video URL or upload a file",
            )

        url = stripped

        # 检查是否已经在处理相同的URL
        if url in processing_urls:
            # 查找现有任务
            for tid, task in tasks.items():
                if task.get("url") == url:
                    return {"task_id": tid, "message": "该视频正在处理中，请等待..."}
            
        # 生成唯一任务ID
        task_id = str(uuid.uuid4())
        
        # 标记URL为正在处理
        processing_urls.add(url)
        
        # 初始化任务状态
        tasks[task_id] = {
            "status": "processing",
            "progress": 0,
            "message": "开始处理视频...",
            "script": None,
            "summary": None,
            "error": None,
            "url": url,  # 保存URL用于去重
            "created_at": time.time(),
        }
        save_tasks(tasks)
        
        # 创建并跟踪异步任务
        task = asyncio.create_task(
            process_video_task(
                task_id, url, summary_language, api_key, model_base_url, model_id, want_video,
                whisper_model, audio_language,
            )
        )
        active_tasks[task_id] = task
        
        return {"task_id": task_id, "message": "任务已创建，正在处理中..."}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"处理视频时出错: {str(e)}")
        raise HTTPException(status_code=500, detail=f"处理失败: {str(e)}")

async def process_video_task(
    task_id: str,
    url: str,
    summary_language: str,
    api_key: str = "",
    model_base_url: str = "",
    model_id: str = "",
    want_video: bool = True,
    whisper_model: str = "",
    audio_language: str = "",
):
    """
    异步处理视频任务
    """
    media_task = None
    try:
        # ── 阶段一：优先尝试获取平台字幕（快速路径） ──────────────────────
        tasks[task_id].update({
            "status": "processing",
            "progress": 10,
            "message": "正在检测视频字幕..."
        })
        save_tasks(tasks)
        await broadcast_task_update(task_id, tasks[task_id])
        await asyncio.sleep(0.1)

        # 原视频下载与字幕/转录/摘要并行，收尾时才等它，尽量不增加总耗时
        if want_video:
            media_task = asyncio.create_task(
                video_processor.download_video(
                    url,
                    TEMP_DIR,
                    f"video_{task_id.replace('-', '')[:12]}",
                    VIDEO_MAX_HEIGHT,
                )
            )

        # 如果前端传入了 API 凭据，创建专用 Summarizer（线程安全，覆盖全局实例）
        if api_key:
            effective_url = model_base_url.rstrip("/") or None
            request_summarizer = Summarizer(
                api_key=api_key,
                base_url=effective_url,
                model=model_id or None,
            )
            logger.info(f"使用前端提供的 API Key，base_url={effective_url}, model={model_id or 'default'}")
        else:
            request_summarizer = summarizer  # 全局实例（使用环境变量）

        subtitle_text, sub_title, sub_lang = await video_processor.fetch_subtitles(url, TEMP_DIR)

        whisper_segments = None
        if subtitle_text:
            # ── 快速路径：有字幕，跳过音频下载和 Whisper ──────────────────
            video_title = sub_title
            raw_script = subtitle_text
            # 把语言写入 transcriber，保持下游逻辑一致
            transcriber.last_detected_language = sub_lang

            tasks[task_id].update({
                "progress": 40,
                "message": f"字幕获取成功（{sub_lang}），正在处理文本..."
            })
            save_tasks(tasks)
            await broadcast_task_update(task_id, tasks[task_id])
        else:
            # ── 慢速路径：无字幕，下载音频 → Whisper 转录 ─────────────────
            tasks[task_id].update({
                "progress": 15,
                "message": "未找到字幕，正在下载视频音频..."
            })
            save_tasks(tasks)
            await broadcast_task_update(task_id, tasks[task_id])

            audio_path = None
            video_title = None

            # 已经在下载原视频时，直接从它抽音轨，避免把同一个视频下载两遍
            if media_task is not None:
                video_path, media_title = await media_task
                if video_path:
                    try:
                        audio_path = await video_processor.normalize_local_media_to_m4a(
                            Path(video_path), TEMP_DIR
                        )
                        video_title = media_title or sub_title or "unknown"
                        logger.info("复用原视频抽取音轨，跳过独立音频下载")
                    except Exception as e:
                        logger.warning(f"从原视频抽音轨失败，回退独立音频下载: {e}")
                        audio_path = None

            if audio_path is None:
                audio_path, video_title = await video_processor.download_and_convert(
                    url, TEMP_DIR, prefetched_title=sub_title or None
                )

            tasks[task_id].update({
                "progress": 35,
                "message": "音频下载完成，准备转录..."
            })
            save_tasks(tasks)
            await broadcast_task_update(task_id, tasks[task_id])

            tasks[task_id].update({
                "progress": 40,
                "message": "正在转录音频（Whisper）..."
            })
            save_tasks(tasks)
            await broadcast_task_update(task_id, tasks[task_id])

            req_transcriber = get_transcriber(whisper_model)
            raw_script = await req_transcriber.transcribe(audio_path, language=audio_language or None)
            # 下游 _run_post_extract_pipeline 从全局实例读语言，保持同步
            transcriber.last_detected_language = req_transcriber.last_detected_language
            whisper_segments = list(req_transcriber.last_segments)
            whisper_segments, raw_script = await _maybe_diarize(
                task_id, audio_path, whisper_segments, raw_script
            )

        await _run_post_extract_pipeline(
            task_id=task_id,
            raw_script=raw_script,
            video_title=video_title,
            source_ref=url,
            summary_language=summary_language,
            request_summarizer=request_summarizer,
            dedup_url=url,
            api_key=api_key,
            model_base_url=model_base_url,
            model_id=model_id,
            media_task=media_task,
            segments=whisper_segments,
        )

        # 不要立即删除临时文件！保留给用户下载
        # 文件会在一定时间后自动清理或用户手动清理

    except Exception as e:
        logger.error(f"任务 {task_id} 处理失败: {str(e)}")
        # 主流程已失败，不要让原视频下载在后台继续跑
        if media_task is not None and not media_task.done():
            media_task.cancel()
        # 从处理列表中移除URL
        processing_urls.discard(url)
        
        # 从活跃任务列表中移除
        if task_id in active_tasks:
            del active_tasks[task_id]
            
        tasks[task_id].update({
            "status": "error",
            "error": str(e),
            "message": f"处理失败: {str(e)}"
        })
        save_tasks(tasks)
        await broadcast_task_update(task_id, tasks[task_id])

@app.post("/api/process-upload")
async def process_upload(
    file: UploadFile = File(...),
    summary_language: str = Form(default="zh"),
    api_key: str = Form(default=""),
    model_base_url: str = Form(default=""),
    model_id: str = Form(default=""),
    whisper_model: str = Form(default=""),
    audio_language: str = Form(default=""),
):
    """独立上传入口；逻辑与 multipart 带 file 的 /api/process-video 相同。"""
    return await _enqueue_upload_job(
        file, summary_language, api_key, model_base_url, model_id,
        whisper_model, _sanitize_audio_language(audio_language),
    )


async def process_upload_task(
    task_id: str,
    saved_path: Path,
    original_name: str,
    video_title: str,
    ext_lower: str,
    summary_language: str,
    api_key: str = "",
    model_base_url: str = "",
    model_id: str = "",
    whisper_model: str = "",
    audio_language: str = "",
):
    source_ref = f"upload:{original_name}"
    try:
        if api_key:
            effective_url = model_base_url.rstrip("/") or None
            request_summarizer = Summarizer(
                api_key=api_key,
                base_url=effective_url,
                model=model_id or None,
            )
            logger.info(
                f"上传任务使用前端 API Key，base_url={effective_url}, model={model_id or 'default'}"
            )
        else:
            request_summarizer = summarizer

        whisper_segments = None
        if ext_lower == ".txt":
            tasks[task_id].update({
                "progress": 20,
                "message": "正在读取文本文件...",
            })
            save_tasks(tasks)
            await broadcast_task_update(task_id, tasks[task_id])

            body = saved_path.read_text(encoding="utf-8", errors="replace")
            if not body.strip():
                raise Exception("文本文件为空")
            transcriber.last_detected_language = None
            raw_script = _txt_to_raw_transcript_markdown(body)
        else:
            tasks[task_id].update({
                "progress": 15,
                "message": "正在转换音频格式...",
            })
            save_tasks(tasks)
            await broadcast_task_update(task_id, tasks[task_id])

            audio_path = await video_processor.normalize_local_media_to_m4a(saved_path, TEMP_DIR)

            tasks[task_id].update({
                "progress": 35,
                "message": "音频准备完成，准备转录...",
            })
            save_tasks(tasks)
            await broadcast_task_update(task_id, tasks[task_id])

            tasks[task_id].update({
                "progress": 40,
                "message": "正在转录音频（Whisper）...",
            })
            save_tasks(tasks)
            await broadcast_task_update(task_id, tasks[task_id])

            req_transcriber = get_transcriber(whisper_model)
            raw_script = await req_transcriber.transcribe(audio_path, language=audio_language or None)
            transcriber.last_detected_language = req_transcriber.last_detected_language
            whisper_segments = list(req_transcriber.last_segments)
            whisper_segments, raw_script = await _maybe_diarize(
                task_id, audio_path, whisper_segments, raw_script
            )

        # 上传的原件本身就是「原视频」，无需再下载，直接挂到结果上
        is_media_upload = ext_lower != ".txt"

        await _run_post_extract_pipeline(
            task_id=task_id,
            raw_script=raw_script,
            video_title=video_title,
            source_ref=source_ref,
            summary_language=summary_language,
            request_summarizer=request_summarizer,
            dedup_url=None,
            api_key=api_key,
            model_base_url=model_base_url,
            model_id=model_id,
            media_filename=saved_path.name if is_media_upload else None,
            media_download_name=original_name if is_media_upload else None,
            segments=whisper_segments,
        )

    except Exception as e:
        logger.error(f"任务 {task_id} 处理失败: {str(e)}")
        if task_id in active_tasks:
            del active_tasks[task_id]
        tasks[task_id].update({
            "status": "error",
            "error": str(e),
            "message": f"处理失败: {str(e)}",
        })
        save_tasks(tasks)
        await broadcast_task_update(task_id, tasks[task_id])


def _local_chat_answer(transcript: str, question: str, top_k: int = 4) -> str:
    """无 LLM 时的本地检索：BM25 找出转录中最相关的段落并原样引用。

    这里**不生成**答案。没有模型就没有生成，把检索结果包装成一段像模像样的
    回答，等于凭空捏造。返回原文片段，让用户自己判断。
    """
    try:
        from local_chat import search_passages

        hits = search_passages(transcript, question, top_k=top_k)
    except Exception as e:
        logger.warning(f"本地检索失败: {e}")
        hits = []

    if not hits:
        return (
            "Aucun passage de la transcription ne correspond à cette question.\n\n"
            "*Recherche locale, sans IA. Pour une réponse rédigée, ajoutez une clé API "
            "dans Paramètres.*"
        )

    excerpts = "\n\n".join(f"> {h['text']}" for h in hits)
    return (
        f"**Passages les plus pertinents de la transcription** :\n\n{excerpts}\n\n"
        "*Recherche locale, sans IA : ces extraits sont cités tels quels, ils ne sont "
        "pas une réponse rédigée. Ajoutez une clé API dans Paramètres pour une synthèse.*"
    )


@app.post("/api/chat")
async def chat_with_transcript(
    task_id: str = Form(...),
    question: str = Form(...),
    history: str = Form(default="[]"),
    api_key: str = Form(default=""),
    model_base_url: str = Form(default=""),
    model_id: str = Form(default=""),
):
    """基于已完成任务的转录文本回答问题。"""
    task = tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.get("status") != "completed":
        raise HTTPException(status_code=400, detail="任务尚未完成")

    transcript = task.get("script") or ""
    if not transcript.strip():
        raise HTTPException(status_code=400, detail="没有可用的转录文本")

    question = (question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="问题不能为空")

    # 组装客户端：前端凭据优先，否则用服务器全局配置（占位符视为未配置）
    _PLACEHOLDER_KEYS = {"your_openai_api_key_here", "changeme", "sk-xxx", "sk-..."}
    effective_key = (api_key or "").strip() or os.getenv("OPENAI_API_KEY", "").strip()
    if effective_key.lower() in _PLACEHOLDER_KEYS:
        effective_key = ""
    effective_url = (model_base_url or "").strip().rstrip("/") or os.getenv("OPENAI_BASE_URL") or None
    if not effective_key:
        # 无 API Key：本地检索回退 — 返回与问题最相关的转录片段
        return {"answer": _local_chat_answer(transcript, question), "local": True}
    model = (model_id or "").strip() or summarizer.fast_model

    # 转录文本截断，避免超出上下文窗口
    max_chars = int(os.getenv("CHAT_TRANSCRIPT_MAX_CHARS", "48000"))
    excerpt = transcript[:max_chars]

    try:
        prior = json.loads(history or "[]")
        if not isinstance(prior, list):
            prior = []
    except Exception:
        prior = []

    messages = [{
        "role": "system",
        "content": (
            "You are a helpful assistant answering questions about a transcript. "
            "Base your answers strictly on the transcript below. If the answer is not "
            "in the transcript, say so. Answer in the language of the user's question.\n\n"
            f"TRANSCRIPT (video: {task.get('video_title') or 'untitled'}):\n{excerpt}"
        ),
    }]
    for m in prior[-10:]:
        role = m.get("role")
        content = (m.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content[:8000]})
    messages.append({"role": "user", "content": question[:4000]})

    def _ask():
        client = openai.OpenAI(api_key=effective_key, base_url=effective_url)
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.3,
            max_tokens=1500,
        )
        return resp.choices[0].message.content

    try:
        answer = await asyncio.to_thread(_ask)
        return {"answer": answer or ""}
    except Exception as e:
        logger.error(f"chat 调用失败，回退本地检索: {e}")
        # API 失败（无效 Key、配额…）→ 本地检索回退而不是报错
        local = _local_chat_answer(transcript, question)
        return {"answer": f"{local}\n\n*(API indisponible : {e})*", "local": True}


# ── 本地翻译引擎 ─────────────────────────────────────────────────────
# 模型 646 MB，下载**只能由用户在界面上点出来**，绝不在一次转录里悄悄发生。

_translate_download_thread: Optional[threading.Thread] = None


@app.get("/api/translate/status")
async def translate_status():
    """本地翻译模型的状态与下载进度。"""
    from local_translate import get_local_translator

    status = get_local_translator().progress_snapshot()
    status["downloading"] = bool(
        _translate_download_thread and _translate_download_thread.is_alive()
    )
    return status


@app.post("/api/translate/download")
async def translate_download():
    """开始下载本地翻译模型（幂等；已在下载或已就绪时直接返回状态）。"""
    global _translate_download_thread
    from local_translate import get_local_translator

    engine = get_local_translator()
    if engine.is_downloaded:
        return {"started": False, "reason": "already_downloaded", **engine.progress_snapshot()}
    if _translate_download_thread and _translate_download_thread.is_alive():
        return {"started": False, "reason": "already_downloading", **engine.progress_snapshot()}

    def _run():
        try:
            engine.load()          # 下载后顺带加载，首次翻译就不用再等
        except Exception as e:
            logger.error(f"翻译模型准备失败: {e}")

    _translate_download_thread = threading.Thread(
        target=_run, name="translate-model-download", daemon=True
    )
    _translate_download_thread.start()
    return {"started": True, **engine.progress_snapshot()}


# ── 转录库（历史记录）────────────────────────────────────────────────
# 路由顺序有讲究：/api/library/stats 等固定路径必须声明在 /api/library/{entry_id}
# 之前，否则会被当成 entry_id 捕获。


_relocation: dict = {"state": "idle", "copied_bytes": 0, "total_bytes": 0, "target": "", "error": None}
_relocation_lock = threading.Lock()


def _dir_bytes(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            pass
    return total


def _relocate_library(target: Path) -> None:
    """把整个库搬到 target。

    顺序是**先复制、校验、切换，最后才删原件**。这是用户的全部转录和原始音频，
    中途断电也不能出现两边都没有的瞬间。
    """
    global library_store, LIBRARY_DIR
    source = LIBRARY_DIR
    try:
        _relocation.update({"state": "copying", "copied_bytes": 0,
                            "total_bytes": _dir_bytes(source), "target": str(target), "error": None})
        target.mkdir(parents=True, exist_ok=True)

        with _relocation_lock:
            library_store.close()          # 关掉 SQLite，避免复制到写了一半的索引

        for item in source.rglob("*"):
            rel = item.relative_to(source)
            dest = target / rel
            if item.is_dir():
                dest.mkdir(parents=True, exist_ok=True)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(item), str(dest))
            try:
                _relocation["copied_bytes"] += dest.stat().st_size
            except OSError:
                pass

        # 校验：条数对得上才认为搬家成功
        moved_store = LibraryStore(target)
        moved_store.reindex()
        moved_count = moved_store.list(limit=1)[1]
        old_store = LibraryStore(source)
        old_count = old_store.list(limit=1)[1]
        old_store.close()
        if moved_count < old_count:
            moved_store.close()
            raise RuntimeError(f"复制不完整：原 {old_count} 条，新 {moved_count} 条")

        library_store = moved_store
        LIBRARY_DIR = target
        cfg = load_app_config()
        cfg["library_dir"] = str(target)
        save_app_config(cfg)

        shutil.rmtree(str(source), ignore_errors=True)   # 确认无误后才删
        _relocation.update({"state": "done"})
        logger.info(f"转录库已迁移到 {target}")
    except Exception as e:
        logger.error(f"转录库迁移失败: {e}")
        # 失败时把原库重新打开，用户的数据一直在原处，没有丢
        try:
            library_store = LibraryStore(source)
        except Exception:
            pass
        _relocation.update({"state": "error", "error": str(e)})


@app.get("/api/library/location")
async def library_location():
    """当前库位置与所在磁盘的剩余空间。"""
    usage = shutil.disk_usage(str(LIBRARY_DIR)) if LIBRARY_DIR.exists() else None
    return {
        "path": str(LIBRARY_DIR),
        "default_path": str(DATA_ROOT / "library"),
        "is_default": LIBRARY_DIR == DATA_ROOT / "library",
        "free_bytes": usage.free if usage else None,
        "total_bytes": usage.total if usage else None,
        "relocation": dict(_relocation),
    }


@app.post("/api/library/location")
async def set_library_location(payload: dict = Body(...)):
    """更换库位置并搬迁内容。空路径表示恢复默认位置。"""
    if _relocation.get("state") == "copying":
        raise HTTPException(status_code=409, detail="迁移正在进行中")

    raw = (payload.get("path") or "").strip()
    target = (DATA_ROOT / "library") if not raw else Path(raw).expanduser()
    try:
        target = target.resolve()
    except OSError as e:
        raise HTTPException(status_code=400, detail=f"路径无效: {e}")

    if target == LIBRARY_DIR.resolve():
        return {"started": False, "reason": "same_path", "path": str(LIBRARY_DIR)}
    # 目标不能落在当前库里面，否则边复制边被自己吞掉
    if str(target).startswith(str(LIBRARY_DIR.resolve()) + os.sep):
        raise HTTPException(status_code=400, detail="目标目录不能位于当前库内部")

    try:
        target.mkdir(parents=True, exist_ok=True)
        probe = target / ".avt-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as e:
        raise HTTPException(status_code=400, detail=f"目标目录不可写: {e}")

    needed = _dir_bytes(LIBRARY_DIR)
    free = shutil.disk_usage(str(target)).free
    if free < needed * 1.1:
        raise HTTPException(
            status_code=400,
            detail=f"目标磁盘空间不足：需要约 {needed // (1024*1024)} MB，可用 {free // (1024*1024)} MB",
        )

    threading.Thread(target=_relocate_library, args=(target,), daemon=True,
                     name="library-relocate").start()
    return {"started": True, "target": str(target), "total_bytes": needed}


@app.get("/api/library/stats")
async def library_stats():
    """记录数与磁盘占用，供界面的“磁盘空间”面板展示。"""
    return library_store.stats()


@app.post("/api/library/import")
async def library_import(payload: dict = Body(...)):
    """从旧的 localStorage 历史一次性导入（产物文件已丢失，只搬文本）。"""
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise HTTPException(status_code=400, detail="entries 必须是数组")
    imported = await asyncio.to_thread(library_store.import_entries, entries)
    return {"imported": imported, "total": library_store.stats()["count"]}


@app.post("/api/library/free-space")
async def library_free_space(payload: dict = Body(default={})):
    """按 id 列表丢弃媒体文件、保留文本。

    只在用户明确点击“释放空间”时调用：本产品不做任何自动清理。
    不传 ids 时仅返回候选列表，不删除任何内容。
    """
    ids = payload.get("ids")
    if not isinstance(ids, list) or not ids:
        candidates = library_store.purge_candidates(
            older_than_days=float(payload.get("older_than_days") or 0),
            keep_favorites=payload.get("keep_favorites", True),
        )
        return {"candidates": candidates, "freed": 0}

    freed = 0
    for entry_id in ids:
        if isinstance(entry_id, str):
            freed += await asyncio.to_thread(library_store.drop_assets, entry_id)
    return {"freed": freed, "stats": library_store.stats()}


@app.get("/api/library")
async def library_list(
    q: str = "",
    limit: int = 50,
    offset: int = 0,
    favorites_only: bool = False,
):
    """分页列出记录（仅元数据，不含全文），按创建时间倒序。"""
    items, total = library_store.list(q=q, limit=limit, offset=offset, favorites_only=favorites_only)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@app.get("/api/library/{entry_id}")
async def library_get(entry_id: str):
    """完整记录：元数据 + 全文（转录、摘要、翻译、分段）。"""
    record = library_store.get(entry_id)
    if not record:
        raise HTTPException(status_code=404, detail="记录不存在")
    return record


@app.patch("/api/library/{entry_id}")
async def library_update(entry_id: str, payload: dict = Body(...)):
    """重命名（title）或标星（favorite）。"""
    title = payload.get("title")
    favorite = payload.get("favorite")
    if title is None and favorite is None:
        raise HTTPException(status_code=400, detail="没有可更新的字段")
    if title is not None and not str(title).strip():
        raise HTTPException(status_code=400, detail="标题不能为空")
    updated = library_store.update(
        entry_id,
        title=str(title).strip() if title is not None else None,
        favorite=bool(favorite) if favorite is not None else None,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="记录不存在")
    return updated


@app.delete("/api/library/{entry_id}")
async def library_delete(entry_id: str):
    """彻底删除一条记录及其全部文件。仅由用户主动触发。"""
    if not await asyncio.to_thread(library_store.delete, entry_id):
        raise HTTPException(status_code=404, detail="记录不存在")
    return {"deleted": entry_id, "stats": library_store.stats()}


@app.get("/api/library/{entry_id}/asset/{kind}")
async def library_asset(entry_id: str, kind: str, download: bool = False):
    """取记录的产物文件（audio/video/srt/vtt/raw/script/summary/translation）。

    download=false（默认）内联播放，FileResponse 自带 Range 支持；
    download=true 时按记录标题生成下载名。
    """
    path = library_store.asset_path(entry_id, kind)
    if path is None:
        raise HTTPException(status_code=404, detail="文件不存在")

    ext = path.suffix.lower()
    media_type = TEXT_DOWNLOAD_MIME.get(ext) or MEDIA_MIME.get(ext) or "application/octet-stream"
    if not download:
        return FileResponse(
            path,
            media_type=media_type,
            headers={"Accept-Ranges": "bytes", "Cache-Control": "private, max-age=3600"},
        )

    meta = library_store.get_meta(entry_id) or {}
    stem = _sanitize_title_for_filename(meta.get("title") or entry_id)
    return FileResponse(path, media_type=media_type, filename=f"{stem}{ext}")


@app.get("/api/history")
async def get_history(limit: int = 100):
    """已完成任务的紧凑列表。**已废弃**：新界面请用 /api/library。"""
    items = []
    for tid, t in tasks.items():
        if t.get("status") != "completed":
            continue
        items.append({
            "task_id": tid,
            "video_title": t.get("video_title") or "(untitled)",
            "url": t.get("url"),
            "created_at": t.get("created_at"),
            "detected_language": t.get("detected_language"),
            "summary_language": t.get("summary_language"),
            "has_translation": bool(t.get("translation")),
            "has_subtitles": bool(t.get("srt_filename")),
        })
    items.sort(key=lambda x: x.get("created_at") or 0, reverse=True)
    return {"items": items[:max(1, min(limit, 500))]}


@app.get("/api/task-status/{task_id}")
async def get_task_status(task_id: str):
    """
    获取任务状态
    """
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    return tasks[task_id]

@app.get("/api/task-stream/{task_id}")
async def task_stream(task_id: str):
    """
    SSE实时任务状态流
    """
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    async def event_generator():
        # 创建任务专用的队列
        queue = asyncio.Queue()
        
        # 将队列添加到连接列表
        if task_id not in sse_connections:
            sse_connections[task_id] = []
        sse_connections[task_id].append(queue)
        
        try:
            # 立即发送当前状态
            current_task = tasks.get(task_id, {})
            yield f"data: {json.dumps(current_task, ensure_ascii=False)}\n\n"
            
            # 持续监听状态更新
            while True:
                try:
                    # 等待状态更新，超时时间30秒发送心跳
                    data = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {data}\n\n"
                    
                    # 如果任务完成或失败，结束流
                    task_data = json.loads(data)
                    if task_data.get("status") in ["completed", "error"]:
                        break
                        
                except asyncio.TimeoutError:
                    # 发送心跳保持连接
                    yield f"data: {json.dumps({'type': 'heartbeat'}, ensure_ascii=False)}\n\n"
                    
        except asyncio.CancelledError:
            logger.info(f"SSE连接被取消: {task_id}")
        except Exception as e:
            logger.error(f"SSE流异常: {e}")
        finally:
            # 清理连接
            if task_id in sse_connections and queue in sse_connections[task_id]:
                sse_connections[task_id].remove(queue)
                if not sse_connections[task_id]:
                    del sse_connections[task_id]
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET",
            "Access-Control-Allow-Headers": "Cache-Control"
        }
    )

TEXT_DOWNLOAD_MIME = {
    ".md": "text/markdown",
    ".srt": "application/x-subrip",
    ".vtt": "text/vtt",
}
DOWNLOAD_ALLOWED_EXT = frozenset(TEXT_DOWNLOAD_MIME) | frozenset(MEDIA_MIME)


@app.get("/api/download/{filename}")
async def download_file(filename: str, name: str = ""):
    """
    直接从temp目录下载文件（转录 .md 与原视频/音频）。

    name: 可选，前端建议的展示文件名（会被清洗，扩展名强制与实际文件一致）。
    """
    try:
        file_path = _resolve_temp_file(filename, DOWNLOAD_ALLOWED_EXT)
        ext = file_path.suffix.lower()
        media_type = TEXT_DOWNLOAD_MIME.get(ext) or MEDIA_MIME[ext]
        download_name = _safe_download_name(name, ext) or filename

        return FileResponse(
            file_path,
            filename=download_name,
            media_type=media_type,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"下载文件失败: {e}")
        raise HTTPException(status_code=500, detail=f"下载失败: {str(e)}")


@app.get("/api/media/{filename}")
async def stream_media(filename: str):
    """
    内联播放原视频/音频，供结果页的 <video>/<audio> 直接引用。

    不设置 Content-Disposition，浏览器才会内联播放而不是触发下载；
    FileResponse 本身支持 Range 请求，所以进度条可以拖动。
    """
    try:
        file_path = _resolve_temp_file(filename, frozenset(MEDIA_MIME))
        return FileResponse(
            file_path,
            media_type=MEDIA_MIME[file_path.suffix.lower()],
            headers={"Accept-Ranges": "bytes", "Cache-Control": "private, max-age=3600"},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"读取媒体文件失败: {e}")
        raise HTTPException(status_code=500, detail=f"读取失败: {str(e)}")


@app.delete("/api/task/{task_id}")
async def delete_task(task_id: str):
    """
    取消并删除任务
    """
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    # 如果任务还在运行，先取消它
    if task_id in active_tasks:
        task = active_tasks[task_id]
        if not task.done():
            task.cancel()
            logger.info(f"任务 {task_id} 已被取消")
        del active_tasks[task_id]
    
    # 从处理URL列表中移除
    task_url = tasks[task_id].get("url")
    if task_url:
        processing_urls.discard(task_url)
    
    # 删除任务记录
    del tasks[task_id]
    return {"message": "任务已取消并删除"}

@app.get("/api/tasks/active")
async def get_active_tasks():
    """
    获取当前活跃任务列表（用于调试）
    """
    active_count = len(active_tasks)
    processing_count = len(processing_urls)
    return {
        "active_tasks": active_count,
        "processing_urls": processing_count,
        "task_ids": list(active_tasks.keys())
    }

# ── 实时转录：浏览器麦克风 → 本服务代理 → OpenAI Realtime API ──────────────
# 代理模式：API Key 只在服务端使用，绝不下发到浏览器之外的第三方。
OPENAI_REALTIME_URL = "wss://api.openai.com/v1/realtime?intent=transcription"
LIVE_TRANSCRIBE_MODELS = {"gpt-4o-mini-transcribe", "gpt-4o-transcribe", "whisper-1"}


@app.websocket("/ws/live-transcribe")
async def live_transcribe(ws: WebSocket):
    """
    实时转录 WebSocket。协议：
      客户端 → 服务端: {"type":"start","api_key":...,"model"?,"language"?}
                       {"type":"audio","audio":"<base64 pcm16 24kHz mono>"}
                       {"type":"stop"}
      服务端 → 客户端: {"type":"ready"} / {"type":"delta","text"} /
                       {"type":"utterance","text"} / {"type":"error","message"}
    """
    if not _ws_token_ok(ws):
        await ws.close(code=1008)
        return

    await ws.accept()
    oai = None
    try:
        try:
            import websockets
        except ImportError:
            await ws.send_json({"type": "error", "message": "Server missing 'websockets' package (pip install websockets)"})
            return

        try:
            cfg = json.loads(await asyncio.wait_for(ws.receive_text(), timeout=15))
        except (asyncio.TimeoutError, json.JSONDecodeError):
            await ws.send_json({"type": "error", "message": "Expected start message"})
            return

        api_key = (cfg.get("api_key") or "").strip()
        if cfg.get("type") != "start" or not api_key:
            await ws.send_json({"type": "error", "message": "OpenAI API key required"})
            return

        model = cfg.get("model") or "gpt-4o-mini-transcribe"
        if model not in LIVE_TRANSCRIBE_MODELS:
            model = "gpt-4o-mini-transcribe"
        language = (cfg.get("language") or "").strip()

        headers = {"Authorization": f"Bearer {api_key}", "OpenAI-Beta": "realtime=v1"}
        try:
            oai = await websockets.connect(
                OPENAI_REALTIME_URL, additional_headers=headers, max_size=16 * 1024 * 1024
            )
        except TypeError:
            # websockets < 13 用 extra_headers
            oai = await websockets.connect(
                OPENAI_REALTIME_URL, extra_headers=headers, max_size=16 * 1024 * 1024
            )

        transcription_cfg = {"model": model}
        if language:
            transcription_cfg["language"] = language
        await oai.send(json.dumps({
            "type": "transcription_session.update",
            "session": {
                "input_audio_format": "pcm16",
                "input_audio_transcription": transcription_cfg,
                "turn_detection": {"type": "server_vad", "silence_duration_ms": 600},
            },
        }))
        await ws.send_json({"type": "ready"})

        async def pump_client():
            """浏览器 → OpenAI：转发音频块，stop 即结束。"""
            while True:
                data = json.loads(await ws.receive_text())
                mtype = data.get("type")
                if mtype == "audio" and data.get("audio"):
                    await oai.send(json.dumps({
                        "type": "input_audio_buffer.append",
                        "audio": data["audio"],
                    }))
                elif mtype == "stop":
                    return

        async def pump_openai():
            """OpenAI → 浏览器：只转发转录相关事件。"""
            async for raw in oai:
                evt = json.loads(raw)
                etype = evt.get("type", "")
                if etype == "conversation.item.input_audio_transcription.delta":
                    await ws.send_json({"type": "delta", "text": evt.get("delta", "")})
                elif etype == "conversation.item.input_audio_transcription.completed":
                    await ws.send_json({"type": "utterance", "text": evt.get("transcript", "")})
                elif etype == "error":
                    err = evt.get("error") or {}
                    await ws.send_json({"type": "error", "message": err.get("message", "OpenAI realtime error")})

        client_task = asyncio.create_task(pump_client())
        openai_task = asyncio.create_task(pump_openai())
        try:
            done, pending = await asyncio.wait(
                {client_task, openai_task}, return_when=asyncio.FIRST_COMPLETED
            )
            for p in pending:
                p.cancel()
            for d in done:
                exc = d.exception()
                if exc and not isinstance(exc, (WebSocketDisconnect, asyncio.CancelledError)):
                    raise exc
        finally:
            for t in (client_task, openai_task):
                if not t.done():
                    t.cancel()

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"实时转录会话失败: {e}")
        try:
            await ws.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass
    finally:
        if oai is not None:
            try:
                await oai.close()
            except Exception:
                pass
        try:
            await ws.close()
        except Exception:
            pass


# ── 实时转录（本地，无需 API Key）：faster-whisper 周期性转录累积音频 ────────
LIVE_LOCAL_SAMPLE_RATE = 24000          # 与前端 pcm-worklet 输出一致
LIVE_LOCAL_BYTES_PER_SEC = LIVE_LOCAL_SAMPLE_RATE * 2  # PCM16 mono
LIVE_LOCAL_MAX_SECONDS = 600            # 10 min 上限
LIVE_LOCAL_PASS_MIN_NEW_SECONDS = 3.0   # 每次至少累积 3 秒新音频才重新转录


def _write_pcm16_wav(path: Path, pcm: bytes, sample_rate: int = LIVE_LOCAL_SAMPLE_RATE) -> None:
    import wave
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)


@app.websocket("/ws/live-local")
async def live_local_transcribe(ws: WebSocket):
    """
    实时转录（本地 faster-whisper，无需任何 API Key）。协议与 /ws/live-transcribe 一致：
      客户端 → {"type":"start","whisper_model"?,"language"?}
               {"type":"audio","audio":"<base64 pcm16 24kHz mono>"}
               {"type":"stop"}
      服务端 → {"type":"ready"} / {"type":"partial","text"}（全文替换式刷新）
               {"type":"utterance","text"}（最终结果）/ {"type":"error","message"}
    说明：整段缓冲重转录，间隔自适应（至少 3 秒新音频且上一轮已结束）。
    """
    if not _ws_token_ok(ws):
        await ws.close(code=1008)
        return

    import base64

    await ws.accept()
    session_id = uuid.uuid4().hex[:12]
    wav_path = TEMP_DIR / f"live_local_{session_id}.wav"
    buf = bytearray()
    state = {"busy": False, "last_len": 0}

    try:
        try:
            cfg = json.loads(await asyncio.wait_for(ws.receive_text(), timeout=15))
        except (asyncio.TimeoutError, json.JSONDecodeError):
            await ws.send_json({"type": "error", "message": "Expected start message"})
            return
        if cfg.get("type") != "start":
            await ws.send_json({"type": "error", "message": "Expected start message"})
            return

        req_transcriber = get_transcriber(cfg.get("whisper_model") or "")
        language = _sanitize_audio_language(cfg.get("language") or "") or None
        max_bytes = LIVE_LOCAL_MAX_SECONDS * LIVE_LOCAL_BYTES_PER_SEC
        await ws.send_json({"type": "ready"})

        async def run_pass(final: bool) -> None:
            if state["busy"]:
                return
            state["busy"] = True
            try:
                data = bytes(buf)
                state["last_len"] = len(data)
                if len(data) < int(0.5 * LIVE_LOCAL_BYTES_PER_SEC):
                    return
                _write_pcm16_wav(wav_path, data)
                text = await req_transcriber.transcribe_plain(str(wav_path), language=language)
                if final:
                    await ws.send_json({"type": "utterance", "text": text})
                elif text:
                    await ws.send_json({"type": "partial", "text": text})
            except Exception as e:
                logger.warning(f"本地实时转录批次失败: {e}")
                if final:
                    await ws.send_json({"type": "error", "message": str(e)})
            finally:
                state["busy"] = False

        pending: set = set()
        while True:
            data = json.loads(await ws.receive_text())
            mtype = data.get("type")
            if mtype == "audio" and data.get("audio"):
                try:
                    buf.extend(base64.b64decode(data["audio"]))
                except Exception:
                    continue
                if len(buf) > max_bytes:
                    await ws.send_json({"type": "error", "message": f"Session over {LIVE_LOCAL_MAX_SECONDS//60} min limit"})
                    break
                new_bytes = len(buf) - state["last_len"]
                if not state["busy"] and new_bytes >= LIVE_LOCAL_PASS_MIN_NEW_SECONDS * LIVE_LOCAL_BYTES_PER_SEC:
                    t = asyncio.create_task(run_pass(final=False))
                    pending.add(t)
                    t.add_done_callback(pending.discard)
            elif mtype == "stop":
                break

        # 等上一轮结束后做最终完整转录
        while state["busy"]:
            await asyncio.sleep(0.1)
        state["last_len"] = -1  # 强制最终轮
        await run_pass(final=True)

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"本地实时转录会话失败: {e}")
        try:
            await ws.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass
    finally:
        try:
            wav_path.unlink(missing_ok=True)
        except Exception:
            pass
        try:
            await ws.close()
        except Exception:
            pass


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
