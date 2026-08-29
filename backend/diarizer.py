import os
import asyncio
import logging

logger = logging.getLogger(__name__)


class Diarizer:
    """可选的说话人分离（pyannote-audio）。

    仅当 ENABLE_DIARIZATION=1 且提供 HF_TOKEN（或 HUGGINGFACE_TOKEN）时启用。
    pyannote 未安装或模型加载失败时静默降级（转录不带说话人标签）。
    """

    def __init__(self):
        self.pipeline = None
        self._load_attempted = False

    @property
    def enabled(self) -> bool:
        flag = os.getenv("ENABLE_DIARIZATION", "").strip().lower() in ("1", "true", "yes")
        token = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_TOKEN")
        return flag and bool(token)

    def _load(self):
        if self.pipeline is None and not self._load_attempted:
            self._load_attempted = True
            try:
                from pyannote.audio import Pipeline  # import 昂贵，延迟到首次使用
                token = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_TOKEN")
                logger.info("正在加载 pyannote speaker-diarization 模型…")
                self.pipeline = Pipeline.from_pretrained(
                    "pyannote/speaker-diarization-3.1",
                    use_auth_token=token,
                )
                logger.info("pyannote 模型加载完成")
            except Exception as e:
                logger.warning(f"说话人分离不可用（pyannote 加载失败）: {e}")
        return self.pipeline

    async def diarize(self, audio_path: str) -> list:
        """返回 [{start, end, speaker}]；不可用时返回 []。"""
        def _run():
            pipe = self._load()
            if pipe is None:
                return []
            diarization = pipe(audio_path)
            turns = []
            for turn, _, speaker in diarization.itertracks(yield_label=True):
                turns.append({
                    "start": float(turn.start),
                    "end": float(turn.end),
                    "speaker": str(speaker),
                })
            return turns

        return await asyncio.to_thread(_run)

    @staticmethod
    def assign_speakers(segments: list, turns: list) -> list:
        """按时间重叠最大原则给每个 Whisper 分段指定说话人（Speaker 1/2/…）。"""
        if not turns:
            return segments

        label_map = {}

        def friendly(raw: str) -> str:
            if raw not in label_map:
                label_map[raw] = f"Speaker {len(label_map) + 1}"
            return label_map[raw]

        for seg in segments:
            best_speaker = None
            best_overlap = 0.0
            for t in turns:
                overlap = min(seg["end"], t["end"]) - max(seg["start"], t["start"])
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_speaker = t["speaker"]
            if best_speaker is not None and best_overlap > 0:
                seg["speaker"] = friendly(best_speaker)
        return segments
