# -*- coding: utf-8 -*-
"""本地抽取式摘要：TextRank（TF-IDF 余弦相似度 + PageRank）。

无 API Key 时使用。相对于原先的词频打分，TextRank 选出的是"与全文联系最紧密"
的句子，而不是"含高频词最多"的句子——后者容易挑中重复啰嗦的句子。

只依赖标准库和 numpy（faster-whisper 已经把 numpy 带进来了，不新增任何依赖）。

对外只有一个函数：

    key_sentences(text, max_sentences=8, lang="") -> list[str]

返回原文中的句子，**保持原文顺序**：摘要要能顺着读下来，按得分排序会读不通。
"""
import math
import re
from typing import List

import numpy as np

# 句子数上限：相似度矩阵是 N×N，不设上限的话一小时的转录稿会吃掉几个 GB。
# 超过则先按信息量（IDF 权重和）预筛，再跑 TextRank。
MAX_SENTENCES_CONSIDERED = 600

# 判定"CJK 文本"的字符占比阈值。中日文没有空格，分词方式完全不同。
CJK_RATIO_THRESHOLD = 0.2

# 句子最短长度。这里只挡明显的碎片（时间戳、残句），不承担"过滤废话"的职责——
# 那是 TextRank 的事：与全文没有共同词的口头禅本来就排不上去。
# CJK 阈值必须留低：日文一句完整的话可能只有六七个字（"最後の文です。"）。
MIN_LEN_LATIN = 20
MIN_LEN_CJK = 6

_CJK_RANGES = (
    (0x3040, 0x30FF),  # 日文假名
    (0x3400, 0x4DBF),  # CJK 扩展 A
    (0x4E00, 0x9FFF),  # CJK 基本区
    (0xF900, 0xFAFF),  # CJK 兼容
    (0xAC00, 0xD7AF),  # 韩文音节
)

# Markdown 噪声：标题行、分隔线、纯粗体标签行（"**语言:** en"）
_MD_NOISE = re.compile(r"^\s*(#{1,6}\s|-{3,}\s*$|\*{2}[^*]+\*{2}\s*:?\s*\S*\s*$|_[^_]+_\s*$)")

# 断句：西文句末标点后跟空白，或 CJK 句末标点，或换行
_SENT_SPLIT = re.compile(r"(?<=[.!?…])\s+|(?<=[。！？；…])|\n+")

# 词：Unicode 词字符（CJK 单字也会被 \w 匹配到，所以 CJK 另走 bigram）
_WORD = re.compile(r"\w+", re.UNICODE)


def _is_cjk_char(ch: str) -> bool:
    code = ord(ch)
    return any(lo <= code <= hi for lo, hi in _CJK_RANGES)


def _is_cjk_text(text: str) -> bool:
    """按 CJK 字符占比判断，而不是按 lang 参数——lang 常常是空的或者不准。"""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    cjk = sum(1 for c in letters if _is_cjk_char(c))
    return cjk / len(letters) >= CJK_RATIO_THRESHOLD


def _strip_markdown_noise(text: str) -> str:
    """去掉标题行和标签行：它们像句子，但摘要里出现毫无意义。"""
    kept = [line for line in text.splitlines() if not _MD_NOISE.match(line)]
    return "\n".join(kept)


def split_sentences(text: str, lang: str = "") -> List[str]:
    """断句。中日韩文没有空格，必须靠句末标点，不能靠空白。"""
    if not text or not text.strip():
        return []

    cleaned = _strip_markdown_noise(text)
    cjk = _is_cjk_text(cleaned)
    min_len = MIN_LEN_CJK if cjk else MIN_LEN_LATIN

    raw = [s.strip() for s in _SENT_SPLIT.split(cleaned)]
    raw = [s for s in raw if s]

    sentences = [s for s in raw if len(s) >= min_len]
    # 全文都很短时（一段口语、一句话视频），宁可放宽也不要返回空。
    if not sentences:
        sentences = [s for s in raw if len(s) >= 2]
    return sentences


def _tokenize(sentence: str, cjk: bool) -> List[str]:
    """CJK 用字符 bigram，其余用词。

    bigram 是中日文检索的常用做法：不需要分词器，且比单字更能区分语义。
    句中的拉丁词（专有名词、缩写）两种模式下都保留。
    """
    lowered = sentence.lower()
    words = [w for w in _WORD.findall(lowered) if not w.isdigit()]

    if not cjk:
        return [w for w in words if len(w) > 1]

    tokens: List[str] = []
    for w in words:
        if any(_is_cjk_char(c) for c in w):
            # CJK 串：切 bigram；单字串（如"人"）保留原字
            if len(w) > 1:
                tokens.extend(w[i : i + 2] for i in range(len(w) - 1))
            else:
                tokens.append(w)
        elif len(w) > 1:
            tokens.append(w)
    return tokens


def _tfidf_matrix(token_lists: List[List[str]]) -> np.ndarray:
    """构造 L2 归一化的 TF-IDF 矩阵，行 = 句子。

    归一化之后，矩阵乘自身的转置就是余弦相似度，不必再逐对计算。
    """
    vocab = {}
    for tokens in token_lists:
        for t in tokens:
            if t not in vocab:
                vocab[t] = len(vocab)

    n_docs = len(token_lists)
    if not vocab or n_docs == 0:
        return np.zeros((n_docs, 0), dtype=np.float64)

    tf = np.zeros((n_docs, len(vocab)), dtype=np.float64)
    for i, tokens in enumerate(token_lists):
        for t in tokens:
            tf[i, vocab[t]] += 1.0

    df = np.count_nonzero(tf, axis=0)
    # 平滑 IDF：df 为 0 不会出现，但加一避免除零，并让全局高频词权重趋近 0
    idf = np.log(1.0 + n_docs / (1.0 + df))
    weighted = tf * idf

    norms = np.linalg.norm(weighted, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    return weighted / norms


def _textrank(similarity: np.ndarray, damping: float = 0.85, max_iter: int = 100, tol: float = 1e-6) -> np.ndarray:
    """在相似度图上跑 PageRank。孤立句子（整行为 0）按均匀分布处理。"""
    n = similarity.shape[0]
    if n == 0:
        return np.zeros(0, dtype=np.float64)
    if n == 1:
        return np.ones(1, dtype=np.float64)

    graph = similarity.copy()
    np.fill_diagonal(graph, 0.0)

    row_sums = graph.sum(axis=1, keepdims=True)
    isolated = (row_sums == 0.0).ravel()
    safe = np.where(row_sums == 0.0, 1.0, row_sums)
    transition = graph / safe
    # 与全文毫无共同词的句子：均匀跳转，否则它会吞掉概率质量
    transition[isolated, :] = 1.0 / n

    scores = np.full(n, 1.0 / n, dtype=np.float64)
    base = (1.0 - damping) / n
    for _ in range(max_iter):
        updated = base + damping * (transition.T @ scores)
        if np.abs(updated - scores).sum() < tol:
            scores = updated
            break
        scores = updated
    return scores


def _preselect(token_lists: List[List[str]], limit: int) -> List[int]:
    """句子过多时的预筛：保留信息量最高的 limit 句，索引按原顺序返回。"""
    df = {}
    for tokens in token_lists:
        for t in set(tokens):
            df[t] = df.get(t, 0) + 1
    n = len(token_lists)
    weights = []
    for i, tokens in enumerate(token_lists):
        score = sum(math.log(1.0 + n / (1.0 + df[t])) for t in set(tokens))
        weights.append((score, -i))
    keep = sorted(range(n), key=lambda i: weights[i], reverse=True)[:limit]
    return sorted(keep)


def key_sentences(text: str, max_sentences: int = 8, lang: str = "") -> List[str]:
    """抽取式摘要：返回最能代表全文的若干句，按原文顺序。

    参数:
        text: 原文（可以是带 Markdown 的转录稿）
        max_sentences: 最多返回几句
        lang: 语言提示，可为空；实际按字符分布判断 CJK，不依赖此参数

    返回:
        原文中的句子列表。文本为空或没有可用句子时返回空列表。
    """
    if max_sentences <= 0:
        return []

    sentences = split_sentences(text, lang)
    if not sentences:
        return []
    if len(sentences) <= max_sentences:
        return sentences

    cjk = _is_cjk_text(text)
    token_lists = [_tokenize(s, cjk) for s in sentences]

    # 预筛，控制 N×N 相似度矩阵的规模
    if len(sentences) > MAX_SENTENCES_CONSIDERED:
        keep = _preselect(token_lists, MAX_SENTENCES_CONSIDERED)
        sentences = [sentences[i] for i in keep]
        token_lists = [token_lists[i] for i in keep]

    matrix = _tfidf_matrix(token_lists)
    if matrix.shape[1] == 0:
        # 没有任何可用词（例如全是数字）：退回取前几句，至少不报错
        return sentences[:max_sentences]

    similarity = matrix @ matrix.T
    scores = _textrank(similarity)

    # 得分并列时按出现顺序取靠前的，保证结果可复现
    ranked = sorted(range(len(sentences)), key=lambda i: (-scores[i], i))
    chosen = sorted(ranked[:max_sentences])
    return [sentences[i] for i in chosen]
