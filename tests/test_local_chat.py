# -*- coding: utf-8 -*-
"""backend/local_chat.py 的回归测试。

只用标准库和 numpy：
    python -m unittest discover -s tests

检索和摘要一样，是"错了也看着像对的"算法——返回任意几段都像结果。所以用例
针对的是**只有 BM25 才成立的性质**，简单的词项重合计数会挂在这几条上：
  - IDF：命中一个罕见词，胜过命中一堆常见词
  - 长度归一化：长段落不会仅仅因为长就赢
  - 没有命中就返回空，而不是凑数
带 REGRESSION 标记的用例都在故意改坏代码后验证过会失败。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from local_chat import PASSAGE_MAX_CHARS, build_passages, search_passages  # noqa: E402


class TestBuildPassages(unittest.TestCase):
    def test_splits_on_blank_lines_and_keeps_order(self):
        transcript = "Premier paragraphe du transcript.\n\nDeuxieme paragraphe.\n\nTroisieme paragraphe."
        passages = build_passages(transcript)
        self.assertEqual(len(passages), 3)
        self.assertIn("Premier", passages[0])
        self.assertIn("Troisieme", passages[2])

    def test_long_block_is_regrouped_by_sentences(self):
        phrase = "This sentence carries enough words to be considered a real sentence here. "
        passages = build_passages(phrase * 40)
        self.assertGreater(len(passages), 1)
        for p in passages:
            self.assertLessEqual(len(p), PASSAGE_MAX_CHARS)

    def test_unpunctuated_wall_of_text_is_hard_split(self):
        """没有任何标点的长串也不能变成一个巨型段落。"""
        passages = build_passages("mot " * 900)
        self.assertGreater(len(passages), 1)
        for p in passages:
            self.assertLessEqual(len(p), PASSAGE_MAX_CHARS)

    def test_empty_transcript_returns_empty(self):
        self.assertEqual(build_passages(""), [])
        self.assertEqual(build_passages("  \n\n "), [])


class TestSearchPassages(unittest.TestCase):
    TRANSCRIPT = (
        "The team discussed the deployment process and the deployment schedule "
        "and the deployment risks and the deployment rollback plan in detail.\n\n"
        "We finally decided to run the workload on Kubernetes for the next quarter.\n\n"
        "The meeting ended with a summary of the deployment process once again.\n"
    )

    def test_finds_the_passage_containing_the_answer(self):
        resultats = search_passages(self.TRANSCRIPT, "Where does the workload run?", top_k=1)
        self.assertEqual(len(resultats), 1)
        self.assertIn("Kubernetes", resultats[0]["text"])

    # 三段长度相近，只有词的稀有度不同：这样长度归一化帮不上忙，
    # 胜负完全取决于 IDF。段落长度相当是这条用例成立的前提。
    TRANSCRIPT_IDF = (
        "The deployment process was reviewed again during the deployment review meeting.\n\n"
        "The workload finally moved to Kubernetes during the same review meeting.\n\n"
        "Another deployment discussion filled the rest of the deployment review meeting.\n"
    )

    def test_rare_term_beats_repeated_common_terms(self):
        """REGRESSION: 去掉 IDF（把权重固定为 1）后，这条必挂。

        问题里既有罕见词 Kubernetes（只出现一次），也有全文重复的 deployment
        （在另外两段各出现两次）。按词项重合计数，重复最多的段落会赢；
        按 BM25，含罕见词的那段赢——后者才是用户要找的。
        """
        resultats = search_passages(self.TRANSCRIPT_IDF, "deployment Kubernetes", top_k=3)
        self.assertTrue(resultats)
        self.assertIn("Kubernetes", resultats[0]["text"])

    def test_shorter_passage_wins_at_equal_term_count(self):
        """REGRESSION: 关掉长度归一化（b=0）后，这条必挂。

        断言必须用相等而不是包含：短段落是长段落的前缀，用 assertIn
        两种情况都会通过——这条用例最初就是这样写错的。
        """
        court = "Kubernetes runs the workload."
        long = "Kubernetes runs the workload. " + "Unrelated filler sentence about something else. " * 12
        transcript = f"{long}\n\n{court}"
        resultats = search_passages(transcript, "Kubernetes workload", top_k=2)
        self.assertEqual(len(resultats), 2)
        self.assertEqual(resultats[0]["text"].strip(), court)

    def test_scores_are_descending_and_positive(self):
        resultats = search_passages(self.TRANSCRIPT, "deployment rollback plan", top_k=3)
        self.assertTrue(resultats)
        scores = [r["score"] for r in resultats]
        self.assertEqual(scores, sorted(scores, reverse=True))
        for s in scores:
            self.assertGreater(s, 0.0)

    def test_respects_top_k(self):
        for k in (1, 2, 3):
            self.assertLessEqual(len(search_passages(self.TRANSCRIPT, "deployment", top_k=k)), k)

    def test_top_k_zero_or_negative_returns_empty(self):
        self.assertEqual(search_passages(self.TRANSCRIPT, "deployment", top_k=0), [])
        self.assertEqual(search_passages(self.TRANSCRIPT, "deployment", top_k=-2), [])

    def test_no_match_returns_empty_rather_than_filler(self):
        """REGRESSION: 宁可空手而归，也不要返回不相关的段落让人以为找到了。"""
        self.assertEqual(search_passages(self.TRANSCRIPT, "photosynthese chlorophylle", top_k=4), [])

    def test_empty_question_returns_empty(self):
        self.assertEqual(search_passages(self.TRANSCRIPT, "", top_k=4), [])
        self.assertEqual(search_passages(self.TRANSCRIPT, "   ?  ", top_k=4), [])

    def test_empty_transcript_returns_empty(self):
        self.assertEqual(search_passages("", "Kubernetes", top_k=4), [])

    def test_returned_text_comes_from_the_transcript(self):
        """检索不是生成：返回的每一段都必须原样出现在转录稿里。"""
        for r in search_passages(self.TRANSCRIPT, "deployment schedule", top_k=3):
            self.assertIn(r["text"].strip()[:40], self.TRANSCRIPT)

    def test_is_deterministic(self):
        premier = search_passages(self.TRANSCRIPT, "deployment on Kubernetes", top_k=3)
        for _ in range(3):
            self.assertEqual(search_passages(self.TRANSCRIPT, "deployment on Kubernetes", top_k=3), premier)

    def test_chinese_question_matches_chinese_passage(self):
        transcript = (
            "会议开始时讨论了部署流程和部署风险以及部署回滚方案。\n\n"
            "最终决定下个季度把服务跑在容器编排平台上面。\n\n"
            "会议结束时又总结了一遍部署流程。\n"
        )
        resultats = search_passages(transcript, "容器编排平台", top_k=1)
        self.assertEqual(len(resultats), 1)
        self.assertIn("容器编排", resultats[0]["text"])


if __name__ == "__main__":
    unittest.main()
