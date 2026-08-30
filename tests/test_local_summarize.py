# -*- coding: utf-8 -*-
"""backend/local_summarize.py 的回归测试。

只用标准库和 numpy：
    python -m unittest discover -s tests

这是一个"错了也看着像对的"算法：随便返回几句话都像摘要。所以这里的用例
不测"有没有输出"，而是测**只有 TextRank 才成立的性质**：
  - 输出必须是原文句子的子集，且保持原文顺序
  - 选中的是与全文联系最紧密的句子，不是排在最前面的句子
  - 中日文按句末标点断句，而不是按空格
每个带 REGRESSION 标记的用例都在故意改坏代码后验证过会失败。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from local_summarize import key_sentences, split_sentences  # noqa: E402


class TestSplitSentences(unittest.TestCase):
    def test_latin_splits_on_terminal_punctuation(self):
        texte = (
            "The first sentence introduces the topic clearly. "
            "The second sentence develops the argument further! "
            "Does the third sentence ask something relevant?"
        )
        phrases = split_sentences(texte)
        self.assertEqual(len(phrases), 3)
        self.assertTrue(phrases[0].endswith("."))
        self.assertTrue(phrases[2].endswith("?"))

    def test_chinese_splits_without_spaces(self):
        """REGRESSION: 中文没有空格，按空白断句会把整段当成一句。"""
        texte = "这段录音讨论了本地转录的实现方式。它不需要任何接口密钥！那么效果如何呢？"
        phrases = split_sentences(texte)
        self.assertEqual(len(phrases), 3)
        self.assertIn("接口密钥", phrases[1])

    def test_japanese_splits_on_cjk_punctuation(self):
        texte = "この録音は日本語の文を含んでいます。句読点で区切られます。最後の文です。"
        phrases = split_sentences(texte)
        self.assertEqual(len(phrases), 3)

    def test_markdown_headings_are_dropped(self):
        """标题和标签行像句子，但出现在摘要里毫无意义。"""
        texte = (
            "# Video Transcription\n"
            "**Detected Language:** en\n"
            "The speaker explains how the local transcription pipeline works today.\n"
        )
        phrases = split_sentences(texte)
        self.assertEqual(len(phrases), 1)
        self.assertIn("speaker explains", phrases[0])

    def test_blank_input_returns_empty(self):
        self.assertEqual(split_sentences(""), [])
        self.assertEqual(split_sentences("   \n\n  "), [])

    def test_short_text_is_not_swallowed_by_length_filter(self):
        """整段都很短时宁可放宽阈值，也不能返回空。"""
        phrases = split_sentences("Oui. Non. Peut-etre.")
        self.assertTrue(phrases)


class TestKeySentences(unittest.TestCase):
    # 两个主题各自重复，外加一句与两个主题都相关的"枢纽句"。
    CLUSTER = (
        "Sourdough fermentation depends heavily on the ambient kitchen temperature.\n"
        "The ambient kitchen temperature controls how sourdough fermentation proceeds.\n"
        "Bakers watch the ambient kitchen temperature to time sourdough fermentation.\n"
        "Ambient kitchen temperature and sourdough fermentation are tightly coupled.\n"
    )
    OUTLIER = "Migratory albatrosses navigate across empty stretches of the southern ocean.\n"

    def test_output_is_a_subset_of_source_sentences(self):
        texte = self.CLUSTER + self.OUTLIER
        phrases = split_sentences(texte)
        for retenue in key_sentences(texte, max_sentences=2):
            self.assertIn(retenue, phrases)

    # 语料是按实测得分排布的，不是凭直觉：得分前二是第 4 句和第 2 句，
    # 也就是说"按得分排序"会输出 [4, 2]，与原文顺序相反。对称的语料抓不住
    # 这个错误——两种写法给出的顺序恰好相同。
    ORDRE = (
        "Migratory albatrosses cross the empty southern ocean every winter.\n"
        "Bakers watch the ambient kitchen temperature during sourdough fermentation.\n"
        "Migratory albatrosses rely on the southern ocean winds every winter.\n"
        "Sourdough fermentation and migratory albatrosses both depend on ambient conditions.\n"
        "Sourdough fermentation depends on the ambient kitchen temperature.\n"
    )

    def test_original_order_is_preserved(self):
        """REGRESSION: 按得分排序会让摘要读不通，必须按原文顺序。"""
        phrases = split_sentences(self.ORDRE)
        retenues = key_sentences(self.ORDRE, max_sentences=2)
        positions = [phrases.index(p) for p in retenues]
        self.assertEqual(positions, sorted(positions))
        # 前提校验：这条语料必须真的"得分序 ≠ 原文序"，否则用例什么也证明不了
        self.assertEqual(positions, [2, 4])

    def test_central_sentence_wins_over_leading_outlier(self):
        """REGRESSION: 排序一旦失效（所有句子同分），结果退化成"取前几句"。
        这里把离题句放在最前面，正是为了抓住这种退化。
        """
        texte = self.OUTLIER + self.CLUSTER
        retenues = key_sentences(texte, max_sentences=1)
        self.assertEqual(len(retenues), 1)
        self.assertNotIn("albatross", retenues[0].lower())
        self.assertIn("sourdough", retenues[0].lower())

    def test_respects_max_sentences(self):
        texte = self.CLUSTER + self.OUTLIER
        for limite in (1, 2, 3):
            self.assertLessEqual(len(key_sentences(texte, max_sentences=limite)), limite)

    def test_max_sentences_zero_or_negative_returns_empty(self):
        self.assertEqual(key_sentences(self.CLUSTER, max_sentences=0), [])
        self.assertEqual(key_sentences(self.CLUSTER, max_sentences=-3), [])

    def test_fewer_sentences_than_requested_returns_all(self):
        texte = "Only one sentence is available in this whole transcript today."
        self.assertEqual(len(key_sentences(texte, max_sentences=8)), 1)

    def test_empty_text_returns_empty(self):
        self.assertEqual(key_sentences("", max_sentences=5), [])
        self.assertEqual(key_sentences("\n\n   \n", max_sentences=5), [])

    def test_is_deterministic(self):
        """同样的输入必须给同样的输出：摘要每次变一点会让用户不信任它。"""
        texte = self.CLUSTER + self.OUTLIER
        premier = key_sentences(texte, max_sentences=2)
        for _ in range(3):
            self.assertEqual(key_sentences(texte, max_sentences=2), premier)

    def test_chinese_summary_returns_source_sentences(self):
        texte = (
            "本地转录不需要任何接口密钥。"
            "整个流程都在这台电脑上完成。"
            "转录完成后可以导出字幕文件。"
            "候鸟每年飞越南方的海洋。"
            "本地转录的速度取决于模型大小。"
        )
        phrases = split_sentences(texte)
        retenues = key_sentences(texte, max_sentences=2)
        self.assertEqual(len(retenues), 2)
        for r in retenues:
            self.assertIn(r, phrases)

    def test_numbers_only_text_does_not_crash(self):
        """全是数字时没有可用词项，必须优雅退化而不是抛异常。"""
        texte = "12 34 56 78. 90 11 22 33. 44 55 66 77. 88 99 10 20."
        retenues = key_sentences(texte, max_sentences=2)
        self.assertLessEqual(len(retenues), 2)

    def test_long_transcript_stays_bounded(self):
        """超过预筛上限的长稿：不能爆内存，也不能返回超量句子。"""
        texte = "\n".join(
            f"Segment number {i} discusses the recurring topic of local transcription quality."
            for i in range(900)
        )
        retenues = key_sentences(texte, max_sentences=5)
        self.assertEqual(len(retenues), 5)


if __name__ == "__main__":
    unittest.main()
