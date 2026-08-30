# -*- coding: utf-8 -*-
"""本地翻译引擎：NLLB-200 + CTranslate2，无需任何 API Key。

为什么可行而且几乎不加负担：ctranslate2、tokenizers、huggingface_hub 已经因
faster-whisper 而存在于依赖与打包产物中。本模块不引入任何新依赖，只多下载一个模型。

模型：CTranslate2 int8 版 NLLB-200-distilled-600M（约 646 MB），一个模型覆盖 200 种
语言，首次翻译时才下载，安装包不因此变大。分词用仓库自带的 tokenizer.json，由已装的
tokenizers 库直接加载，因此不需要 sentencepiece。

设计要点：
  - 懒加载：不翻译就不占内存，也不下载
  - 段落保真：按段落与句子切分后分批翻译，再按原结构拼回
  - 状态可观测：下载与加载状态供界面显示进度
"""

import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# 可用环境变量覆盖（离线部署可指向本地目录）
MODEL_REPO = os.getenv("AVT_TRANSLATE_MODEL", "Serkan007/CTranslate2-nllb-200-int8")
MODEL_SIZE_HINT_BYTES = 646 * 1024 * 1024   # 仅用于界面提示

# 界面语言代码 → NLLB 语言代码。NLLB 用「语言_文字」形式，必须显式给出。
NLLB_CODES: Dict[str, str] = {
    "en": "eng_Latn", "fr": "fra_Latn", "es": "spa_Latn", "de": "deu_Latn",
    "it": "ita_Latn", "pt": "por_Latn", "nl": "nld_Latn", "pl": "pol_Latn",
    "ru": "rus_Cyrl", "uk": "ukr_Cyrl", "tr": "tur_Latn", "ar": "arb_Arab",
    "he": "heb_Hebr", "hi": "hin_Deva", "bn": "ben_Beng", "ur": "urd_Arab",
    "fa": "pes_Arab", "id": "ind_Latn", "ms": "zsm_Latn", "vi": "vie_Latn",
    "th": "tha_Thai", "ja": "jpn_Jpan", "ko": "kor_Hang",
    "zh": "zho_Hans", "zh-cn": "zho_Hans", "zh-tw": "zho_Hant", "yue": "yue_Hant",
    "sv": "swe_Latn", "da": "dan_Latn", "no": "nob_Latn", "fi": "fin_Latn",
    "cs": "ces_Latn", "el": "ell_Grek", "ro": "ron_Latn", "hu": "hun_Latn",
    "sw": "swh_Latn", "ta": "tam_Taml", "te": "tel_Telu", "ml": "mal_Mlym",
}

# 句末标点：拉丁系带空格，CJK 不带空格，两者都要能切
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+|(?<=[。！？；])")
# NLLB 单次输入不宜过长；按字符粗略控制，避免超出位置编码
_MAX_CHUNK_CHARS = 900


def to_nllb_code(lang: str) -> Optional[str]:
    """把界面/Whisper 的语言代码转成 NLLB 代码；无法识别返回 None。"""
    if not lang:
        return None
    key = str(lang).strip().lower().replace("_", "-")
    if key in NLLB_CODES:
        return NLLB_CODES[key]
    base = key.split("-")[0]
    return NLLB_CODES.get(base)


def split_sentences(text: str) -> List[str]:
    """按句切分，中日韩与拉丁系都适用。"""
    parts = [s.strip() for s in _SENTENCE_END.split(text or "") if s and s.strip()]
    return parts or ([text.strip()] if text and text.strip() else [])


def _chunk(sentences: List[str], max_chars: int = _MAX_CHUNK_CHARS) -> List[str]:
    """把句子聚成不超过 max_chars 的块，尽量不拆句。"""
    chunks: List[str] = []
    current = ""
    for sentence in sentences:
        if len(sentence) > max_chars:
            # 单句超长（缺标点的长段）：硬切，宁可切断也不喂爆模型
            if current:
                chunks.append(current)
                current = ""
            for i in range(0, len(sentence), max_chars):
                chunks.append(sentence[i:i + max_chars])
            continue
        candidate = f"{current} {sentence}".strip() if current else sentence
        if len(candidate) > max_chars:
            chunks.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


class ModelUnavailable(RuntimeError):
    """模型不可用（未下载且下载失败，或运行库缺失）。"""


class LocalTranslator:
    """NLLB + CTranslate2 的本地翻译器。线程安全，懒加载。"""

    def __init__(self, repo: str = MODEL_REPO):
        self.repo = repo
        self._translator = None
        self._tokenizer = None
        self._lock = threading.RLock()
        self._status: Dict[str, object] = {"state": "idle", "model": repo}

    # ── 状态 ──────────────────────────────────────────────

    @property
    def status(self) -> Dict[str, object]:
        """供界面轮询：idle / downloading / loading / ready / error。"""
        return dict(self._status)

    def _set_status(self, **fields) -> None:
        self._status.update(fields)

    def model_dir(self) -> Optional[Path]:
        """已下载的模型目录；未下载返回 None（不触发下载）。"""
        local = Path(self.repo)
        if local.is_dir() and (local / "model.bin").is_file():
            return local
        try:
            from huggingface_hub import snapshot_download

            path = snapshot_download(self.repo, local_files_only=True)
            return Path(path)
        except Exception:
            return None

    @property
    def is_downloaded(self) -> bool:
        return self.model_dir() is not None

    @property
    def is_loaded(self) -> bool:
        return self._translator is not None

    def downloaded_bytes(self) -> int:
        """已落盘的字节数，含未完成分片。

        huggingface_hub 的 snapshot_download 不提供进度回调，因此进度只能从磁盘量出来。
        .incomplete 分片必须计入，否则进度条会在下载途中一直显示 0。
        """
        try:
            from huggingface_hub.constants import HF_HUB_CACHE

            folder = Path(HF_HUB_CACHE) / f"models--{self.repo.replace('/', '--')}"
            if not folder.is_dir():
                return 0
            total = 0
            for path in folder.rglob("*"):
                try:
                    if path.is_file() and not path.is_symlink():
                        total += path.stat().st_size
                except OSError:
                    pass
            return total
        except Exception:
            return 0

    def progress_snapshot(self) -> Dict[str, object]:
        """供 /api/translate/status 直接返回的状态。"""
        state = str(self._status.get("state", "idle"))
        if state == "idle" and self.is_downloaded:
            state = "ready" if self.is_loaded else "downloaded"
        got = self.downloaded_bytes()
        return {
            "state": state,
            "model": self.repo,
            "downloaded_bytes": got,
            "total_bytes": MODEL_SIZE_HINT_BYTES,
            "ratio": min(1.0, got / MODEL_SIZE_HINT_BYTES) if MODEL_SIZE_HINT_BYTES else None,
            "is_downloaded": self.is_downloaded,
            "is_loaded": self.is_loaded,
            "error": self._status.get("error"),
        }

    # ── 下载与加载 ────────────────────────────────────────

    def ensure_model(self, progress: Optional[Callable[[Dict[str, object]], None]] = None) -> Path:
        """确保模型在本地，必要时下载。返回模型目录。"""
        with self._lock:
            path = self.model_dir()
            if path is not None:
                return path

            self._set_status(state="downloading", bytes_hint=MODEL_SIZE_HINT_BYTES, started_at=time.time())
            if progress:
                progress(self.status)
            logger.info(f"下载本地翻译模型 {self.repo}（约 {MODEL_SIZE_HINT_BYTES // (1024*1024)} MB）…")
            try:
                from huggingface_hub import snapshot_download

                downloaded = snapshot_download(self.repo)
            except Exception as e:
                self._set_status(state="error", error=str(e))
                logger.error(f"翻译模型下载失败: {e}")
                raise ModelUnavailable(f"下载翻译模型失败: {e}") from e

            self._set_status(state="downloaded")
            if progress:
                progress(self.status)
            return Path(downloaded)

    def load(self, progress: Optional[Callable[[Dict[str, object]], None]] = None) -> None:
        """加载模型与分词器（幂等）。"""
        with self._lock:
            if self._translator is not None:
                return
            path = self.ensure_model(progress)
            self._set_status(state="loading")
            if progress:
                progress(self.status)
            try:
                import ctranslate2
                from tokenizers import Tokenizer

                tokenizer_file = path / "tokenizer.json"
                if not tokenizer_file.is_file():
                    raise ModelUnavailable(f"模型缺少 tokenizer.json: {path}")

                # inter/intra 线程留给 Whisper 一些余量，避免两模型互相饿死
                self._translator = ctranslate2.Translator(
                    str(path), device="cpu", compute_type="int8",
                    inter_threads=1, intra_threads=max(1, (os.cpu_count() or 4) // 2),
                )
                self._tokenizer = Tokenizer.from_file(str(tokenizer_file))
            except ModelUnavailable:
                raise
            except Exception as e:
                self._translator = None
                self._tokenizer = None
                self._set_status(state="error", error=str(e))
                logger.error(f"翻译模型加载失败: {e}")
                raise ModelUnavailable(f"加载翻译模型失败: {e}") from e

            self._set_status(state="ready", error=None)
            if progress:
                progress(self.status)
            logger.info("本地翻译模型就绪")

    def unload(self) -> None:
        """释放模型内存（桌面版内存吃紧时可调用）。"""
        with self._lock:
            self._translator = None
            self._tokenizer = None
            self._set_status(state="idle")

    # ── 翻译 ──────────────────────────────────────────────

    def _translate_chunks(self, chunks: List[str], src: str, dst: str) -> List[str]:
        """一批文本的实际翻译。NLLB 的输入格式为 [源语言标记] … </s>，
        目标语言通过 target_prefix 指定。"""
        encoded = [self._tokenizer.encode(c, add_special_tokens=False).tokens for c in chunks]
        sources = [[src] + tokens + ["</s>"] for tokens in encoded]

        results = self._translator.translate_batch(
            sources,
            target_prefix=[[dst]] * len(sources),
            beam_size=2,          # 2 而非 4：CPU 上速度差一倍，质量差别很小
            max_batch_size=8,
            no_repeat_ngram_size=3,
        )

        out: List[str] = []
        for res in results:
            tokens = res.hypotheses[0]
            if tokens and tokens[0] == dst:
                tokens = tokens[1:]          # 去掉目标语言标记本身
            out.append(self._decode(tokens))
        return out

    def _decode(self, tokens: List[str]) -> str:
        ids = [self._tokenizer.token_to_id(t) for t in tokens]
        ids = [i for i in ids if i is not None]
        return self._tokenizer.decode(ids).strip()

    def translate(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
        progress: Optional[Callable[[Dict[str, object]], None]] = None,
    ) -> str:
        """翻译整段文本，保留段落结构。

        Raises:
            ModelUnavailable: 模型不可用；调用方应回退到「翻译不可用」而不是崩溃。
            ValueError: 语言不受支持。
        """
        src = to_nllb_code(source_lang)
        dst = to_nllb_code(target_lang)
        if not src or not dst:
            raise ValueError(f"语言不受支持: {source_lang} → {target_lang}")
        if src == dst:
            return text
        if not (text or "").strip():
            return ""

        self.load(progress)

        # 段落是原文结构的一部分，逐段翻译再拼回，避免模型把整篇揉成一段
        paragraphs = (text or "").split("\n")
        todo: List[str] = []
        index: List[tuple] = []          # (段落序号, 该段的块数)
        for i, para in enumerate(paragraphs):
            if not para.strip():
                index.append((i, 0))
                continue
            chunks = _chunk(split_sentences(para))
            index.append((i, len(chunks)))
            todo.extend(chunks)

        if not todo:
            return text

        with self._lock:
            translated = self._translate_chunks(todo, src, dst)

        out: List[str] = []
        cursor = 0
        for i, count in index:
            if count == 0:
                out.append("")
                continue
            out.append(" ".join(translated[cursor:cursor + count]).strip())
            cursor += count
        return "\n".join(out)


# 单例：模型很大，绝不重复加载
_translator: Optional[LocalTranslator] = None
_singleton_lock = threading.Lock()


def get_local_translator() -> LocalTranslator:
    global _translator
    with _singleton_lock:
        if _translator is None:
            _translator = LocalTranslator()
        return _translator
