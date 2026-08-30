# -*- coding: utf-8 -*-
"""本地转录稿检索：BM25。

无 API Key 时，"和转录稿对话"退化为**检索**：返回原文中最相关的段落，
不生成任何文字。措辞上必须诚实——没有 LLM 就没有回答，只有出处。
调用方负责把结果呈现为"相关片段"，不要包装成答案。

相对于原先的词项重合计数，BM25 有两点关键差别：
  - IDF：罕见词比常见词值钱。"Kubernetes"命中一次远比"le"命中十次重要。
  - 长度归一化：长段落不会仅仅因为长就赢。

只依赖标准库和 numpy，分词复用 local_summarize（同一作者，同一套规则，
保证摘要和检索对同一段文字的理解一致）。

对外只有一个函数：

    search_passages(transcript, question, top_k=4) -> list[dict]  # {text, score}
"""
import math
import re
from typing import Dict, List

from local_summarize import _is_cjk_text, _tokenize, split_sentences

# BM25 标准参数。k1 控制词频饱和速度，b 控制长度归一化强度。
BM25_K1 = 1.5
BM25_B = 0.75

# 段落目标长度：太短则缺上下文，太长则用户要自己找。按字符算。
PASSAGE_TARGET_CHARS = 480
PASSAGE_MAX_CHARS = 900

_BLANK_LINE = re.compile(r"\n\s*\n")


def build_passages(transcript: str) -> List[str]:
    """把转录稿切成可引用的段落，保持原文顺序。

    先按空行切（转录稿经过分段优化，空行就是语义边界），过长的块再按句子重组。
    """
    if not transcript or not transcript.strip():
        return []

    blocks = [b.strip() for b in _BLANK_LINE.split(transcript)]
    blocks = [b for b in blocks if b]

    passages: List[str] = []
    for block in blocks:
        if len(block) <= PASSAGE_MAX_CHARS:
            passages.append(block)
            continue

        # 过长的块：按句子重组到目标长度附近
        sentences = split_sentences(block) or [block]

        current = ""
        for sentence in sentences:
            # 单句本身就超长（口语没断句、字幕没标点）：硬切。
            # 不处理的话会返回一整屏文字，引用等于没引用。
            if len(sentence) > PASSAGE_MAX_CHARS:
                if current:
                    passages.append(current)
                    current = ""
                passages.extend(
                    sentence[i : i + PASSAGE_TARGET_CHARS]
                    for i in range(0, len(sentence), PASSAGE_TARGET_CHARS)
                )
                continue

            if current and len(current) + len(sentence) + 1 > PASSAGE_TARGET_CHARS:
                passages.append(current)
                current = sentence
            else:
                current = f"{current} {sentence}".strip()
        if current:
            passages.append(current)

    return [p for p in passages if p.strip()]


def _bm25_scores(passage_tokens: List[List[str]], query_tokens: List[str]) -> List[float]:
    """标准 BM25。返回每个段落的得分，与输入顺序一一对应。"""
    n = len(passage_tokens)
    if n == 0 or not query_tokens:
        return [0.0] * n

    lengths = [len(t) for t in passage_tokens]
    avg_len = sum(lengths) / n if n else 0.0
    if avg_len == 0.0:
        return [0.0] * n

    # 词频表 + 文档频率
    tfs: List[Dict[str, int]] = []
    df: Dict[str, int] = {}
    for tokens in passage_tokens:
        counts: Dict[str, int] = {}
        for t in tokens:
            counts[t] = counts.get(t, 0) + 1
        tfs.append(counts)
        for t in counts:
            df[t] = df.get(t, 0) + 1

    scores = [0.0] * n
    for term in set(query_tokens):
        doc_freq = df.get(term, 0)
        if doc_freq == 0:
            continue
        # 带平滑的 IDF：出现在超过一半文档里的词权重迅速下降，但不会变成负数
        idf = math.log(1.0 + (n - doc_freq + 0.5) / (doc_freq + 0.5))
        for i, counts in enumerate(tfs):
            freq = counts.get(term, 0)
            if freq == 0:
                continue
            norm = 1.0 - BM25_B + BM25_B * (lengths[i] / avg_len)
            scores[i] += idf * (freq * (BM25_K1 + 1.0)) / (freq + BM25_K1 * norm)
    return scores


def search_passages(transcript: str, question: str, top_k: int = 4) -> List[dict]:
    """在转录稿中检索与问题最相关的段落。

    参数:
        transcript: 转录稿全文
        question: 用户的问题
        top_k: 最多返回几段

    返回:
        [{"text": 原文段落, "score": BM25 得分}]，按得分降序。
        问题里没有可用词、或没有任何段落命中时返回空列表——
        宁可返回空，也不要返回一堆不相关的段落让人以为找到了。
    """
    if top_k <= 0:
        return []

    passages = build_passages(transcript)
    if not passages:
        return []

    cjk = _is_cjk_text(transcript)
    passage_tokens = [_tokenize(p, cjk) for p in passages]
    query_tokens = _tokenize(question or "", cjk)
    if not query_tokens:
        return []

    scores = _bm25_scores(passage_tokens, query_tokens)

    # 得分并列时按原文顺序，保证结果可复现
    ranked = sorted(range(len(passages)), key=lambda i: (-scores[i], i))
    return [
        {"text": passages[i], "score": round(scores[i], 6)}
        for i in ranked[:top_k]
        if scores[i] > 0.0
    ]
