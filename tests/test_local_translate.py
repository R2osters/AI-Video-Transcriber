# -*- coding: utf-8 -*-
"""本地翻译引擎的测试（backend/local_translate.py）。

模型有 646 MB，测试里绝不下载：CTranslate2 与 tokenizers 都用替身，
验证的是**我们写的那部分**——语言映射、断句、分块、段落还原、NLLB 输入格式、
失败时的行为。模型本身的质量不是单元测试能回答的问题。

    python -m unittest discover -s tests
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import local_translate as lt  # noqa: E402


class FakeEncoding:
    def __init__(self, tokens):
        self.tokens = tokens


class FakeTokenizer:
    """分词器替身：按空白切词，解码时再拼回。记录调用以便断言。"""

    def __init__(self):
        self.encoded = []

    def encode(self, text, add_special_tokens=True):
        self.encoded.append((text, add_special_tokens))
        return FakeEncoding(text.split())

    def token_to_id(self, token):
        return None if token in ("</s>",) else abs(hash(token)) % 100000

    def decode(self, ids):
        return " ".join(f"t{i}" for i in ids)


class FakeResult:
    def __init__(self, hypotheses):
        self.hypotheses = hypotheses


class FakeCT2Translator:
    """CTranslate2 替身：把每个输入原样回显，并记录收到的批次。"""

    def __init__(self):
        self.batches = []

    def translate_batch(self, sources, target_prefix=None, **kwargs):
        self.batches.append({"sources": sources, "target_prefix": target_prefix, "kwargs": kwargs})
        return [FakeResult([[target_prefix[i][0]] + src[1:-1]]) for i, src in enumerate(sources)]


def make_translator():
    """一个已「加载」的翻译器，不碰磁盘也不碰网络。"""
    tr = lt.LocalTranslator()
    tr._tokenizer = FakeTokenizer()
    tr._translator = FakeCT2Translator()
    tr._set_status(state="ready")
    return tr


class TestLanguageCodes(unittest.TestCase):

    def test_common_languages(self):
        self.assertEqual(lt.to_nllb_code("fr"), "fra_Latn")
        self.assertEqual(lt.to_nllb_code("en"), "eng_Latn")
        self.assertEqual(lt.to_nllb_code("ja"), "jpn_Jpan")

    def test_chinese_variants_are_distinct(self):
        """zh-tw ne doit pas retomber sur le chinois simplifié."""
        self.assertEqual(lt.to_nllb_code("zh"), "zho_Hans")
        self.assertEqual(lt.to_nllb_code("zh-CN"), "zho_Hans")
        self.assertEqual(lt.to_nllb_code("zh-TW"), "zho_Hant")

    def test_regional_variant_falls_back_to_base(self):
        self.assertEqual(lt.to_nllb_code("fr-CA"), "fra_Latn")
        self.assertEqual(lt.to_nllb_code("pt_BR"), "por_Latn")

    def test_unknown_and_empty(self):
        self.assertIsNone(lt.to_nllb_code("klingon"))
        self.assertIsNone(lt.to_nllb_code(""))
        self.assertIsNone(lt.to_nllb_code(None))


class TestSentenceSplitting(unittest.TestCase):

    def test_latin_punctuation(self):
        got = lt.split_sentences("Bonjour. Comment ça va ? Très bien !")
        self.assertEqual(len(got), 3)

    def test_cjk_without_spaces(self):
        """Le chinois ne sépare pas ses phrases par des espaces."""
        got = lt.split_sentences("今天天气很好。我们去公园吧！你觉得呢？")
        self.assertEqual(len(got), 3)
        self.assertTrue(got[0].endswith("。"))

    def test_text_without_punctuation_stays_whole(self):
        got = lt.split_sentences("une phrase sans ponctuation finale")
        self.assertEqual(got, ["une phrase sans ponctuation finale"])

    def test_empty(self):
        self.assertEqual(lt.split_sentences(""), [])
        self.assertEqual(lt.split_sentences("   "), [])


class TestChunking(unittest.TestCase):

    def test_sentences_are_grouped_under_the_limit(self):
        sentences = ["a" * 100] * 10
        chunks = lt._chunk(sentences, max_chars=250)
        self.assertTrue(all(len(c) <= 250 for c in chunks), [len(c) for c in chunks])
        self.assertGreater(len(chunks), 1)

    def test_no_sentence_is_lost(self):
        sentences = [f"phrase numero {i}." for i in range(20)]
        joined = " ".join(lt._chunk(sentences, max_chars=80))
        for s in sentences:
            self.assertIn(s, joined)

    def test_oversized_sentence_is_hard_split(self):
        """Une transcription sans ponctuation peut produire une « phrase » énorme :
        mieux vaut la couper que noyer le modèle."""
        chunks = lt._chunk(["x" * 1000], max_chars=300)
        self.assertEqual(len(chunks), 4)
        self.assertTrue(all(len(c) <= 300 for c in chunks))
        self.assertEqual("".join(chunks), "x" * 1000)


class TestTranslation(unittest.TestCase):

    def test_same_language_is_a_noop(self):
        tr = make_translator()
        self.assertEqual(tr.translate("Bonjour", "fr", "fr"), "Bonjour")
        self.assertEqual(tr._translator.batches, [], "aucun appel au modèle ne doit avoir lieu")

    def test_unsupported_language_raises(self):
        tr = make_translator()
        with self.assertRaises(ValueError):
            tr.translate("Bonjour", "fr", "klingon")

    def test_empty_text(self):
        self.assertEqual(make_translator().translate("", "fr", "en"), "")

    def test_nllb_input_format(self):
        """Format attendu par NLLB : [langue source] … </s>, cible en target_prefix."""
        tr = make_translator()
        tr.translate("Bonjour le monde.", "fr", "en")
        batch = tr._translator.batches[0]
        source = batch["sources"][0]
        self.assertEqual(source[0], "fra_Latn", "le jeton de langue source doit ouvrir la séquence")
        self.assertEqual(source[-1], "</s>", "la séquence doit être terminée")
        self.assertEqual(batch["target_prefix"], [["eng_Latn"]])

    def test_tokens_are_encoded_without_special_tokens(self):
        """Les jetons spéciaux sont posés à la main : le tokenizer ne doit pas en ajouter."""
        tr = make_translator()
        tr.translate("Bonjour.", "fr", "en")
        self.assertEqual(tr._tokenizer.encoded[0][1], False)

    def test_paragraph_structure_is_preserved(self):
        tr = make_translator()
        out = tr.translate("Premier paragraphe.\n\nSecond paragraphe.", "fr", "en")
        self.assertEqual(len(out.split("\n")), 3, "la ligne vide entre les paragraphes doit survivre")
        self.assertEqual(out.split("\n")[1], "", "la ligne vide reste vide")

    def test_every_chunk_is_sent_once(self):
        tr = make_translator()
        text = "\n".join("Une phrase de test numéro %d." % i for i in range(5))
        tr.translate(text, "fr", "en")
        sent = len(tr._translator.batches[0]["sources"])
        self.assertEqual(sent, 5, "un bloc par paragraphe non vide")

    def test_blank_lines_do_not_reach_the_model(self):
        tr = make_translator()
        tr.translate("Texte.\n\n\n\nAutre texte.", "fr", "en")
        self.assertEqual(len(tr._translator.batches[0]["sources"]), 2)


class TestModelLifecycle(unittest.TestCase):

    def test_status_starts_idle(self):
        self.assertEqual(lt.LocalTranslator().status["state"], "idle")

    def test_missing_model_reports_unavailable(self):
        """Sans modèle et sans réseau, on lève ModelUnavailable — l'appelant doit
        pouvoir dire « traduction indisponible » plutôt que planter."""
        tr = lt.LocalTranslator(repo="repo/inexistant-pour-de-vrai")
        tr.ensure_model = lambda progress=None: (_ for _ in ()).throw(lt.ModelUnavailable("pas de modèle"))
        with self.assertRaises(lt.ModelUnavailable):
            tr.translate("Bonjour", "fr", "en")

    def test_load_is_idempotent(self):
        tr = make_translator()
        calls = []
        tr.ensure_model = lambda progress=None: calls.append(1)
        tr.load()
        tr.load()
        self.assertEqual(calls, [], "un modèle déjà chargé ne doit pas être rechargé")

    def test_unload_frees_and_resets_status(self):
        tr = make_translator()
        tr.unload()
        self.assertFalse(tr.is_loaded)
        self.assertEqual(tr.status["state"], "idle")

    def test_singleton_is_shared(self):
        """Le modèle pèse 646 Mo : jamais deux instances."""
        self.assertIs(lt.get_local_translator(), lt.get_local_translator())


if __name__ == "__main__":
    unittest.main()
