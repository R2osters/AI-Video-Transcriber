# -*- coding: utf-8 -*-
"""任务完成后归档到转录库的测试（backend/main.py 的 _archive_task_to_library）。

管线有**两个**完成出口：正常出口，以及「未检测到语音」的短路出口。后者走得少，
一旦漏掉归档，用户的录音会静默消失——而它确实会产生真实数据：一次误触麦克风
按钮就足够。两个出口都必须归档。

faster_whisper 在开发机上无法导入（PyAV 损坏），这里注入替身；库本身不需要它。
    python -m unittest discover -s tests
"""

import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def _install_stubs():
    """在导入 main 之前放好 faster_whisper 替身。"""
    if "faster_whisper" not in sys.modules:
        stub = types.ModuleType("faster_whisper")

        class WhisperModel:  # noqa: D401 - 仅占位
            def __init__(self, *args, **kwargs):
                pass

        stub.WhisperModel = WhisperModel
        sys.modules["faster_whisper"] = stub


class ArchiveTestCase(unittest.TestCase):
    """每个用例一套独立的数据目录；main 只导入一次，随后重定向其全局路径。"""

    main = None

    @classmethod
    def setUpClass(cls):
        _install_stubs()
        import os

        cls._boot_dir = tempfile.TemporaryDirectory(prefix="avt_arch_boot_")
        os.environ["AVT_DATA_DIR"] = cls._boot_dir.name
        os.environ["AVT_STATIC_DIR"] = str(_ROOT / "static")
        os.environ.pop("AVT_TOKEN", None)
        sys.path.insert(0, str(_ROOT / "backend"))
        try:
            import main
        except Exception as exc:  # pragma: no cover - dépend de l'environnement
            raise unittest.SkipTest(f"backend/main.py non importable ici : {exc}")
        cls.main = main

    @classmethod
    def tearDownClass(cls):
        if cls.main is not None:
            cls.main.library_store.close()
        cls._boot_dir.cleanup()

    def setUp(self):
        from library import LibraryStore

        self._tmp = tempfile.TemporaryDirectory(prefix="avt_arch_")
        root = Path(self._tmp.name)
        self.temp_dir = root / "temp"
        self.temp_dir.mkdir()

        # On redirige les globales du module vers ce cas de test
        self._saved = (self.main.TEMP_DIR, self.main.library_store, dict(self.main.tasks))
        self.main.TEMP_DIR = self.temp_dir
        self.main.library_store = LibraryStore(root / "library")
        self.main.tasks.clear()
        self.store = self.main.library_store

    def tearDown(self):
        self.store.close()
        temp_dir, store, tasks = self._saved
        self.main.TEMP_DIR = temp_dir
        self.main.library_store = store
        self.main.tasks.clear()
        self.main.tasks.update(tasks)
        self._tmp.cleanup()

    def write_temp(self, name: str, size: int = 500) -> str:
        (self.temp_dir / name).write_bytes(b"\0" * size)
        return name


class TestNormalCompletion(ArchiveTestCase):

    def _completed_task(self, task_id="t-normal"):
        self.main.tasks[task_id] = {
            "status": "completed",
            "video_title": "Ma vidéo",
            "url": "https://www.youtube.com/watch?v=abc",
            "created_at": time.time() - 30,
            "detected_language": "fr",
            "summary_language": "en",
            "script": "# Titre\n\ncontenu",
            "summary": "résumé",
            "translation": "translated",
            "media_filename": self.write_temp("media_ma_video_ab12.m4a", 4000),
            "media_download_name": "Ma video.m4a",
            "media_size_bytes": 4000,
            "srt_filename": self.write_temp("subtitles_ma_video_ab12.srt", 120),
            "vtt_filename": self.write_temp("subtitles_ma_video_ab12.vtt", 130),
            "raw_script_file": self.write_temp("raw_ma_video_ab12.md", 80),
            "script_path": str(self.temp_dir / self.write_temp("transcript_ma_video_ab12.md", 90)),
        }
        return task_id

    def test_entry_is_created_with_metadata(self):
        task_id = self._completed_task()
        self.main._archive_task_to_library(task_id, segments=[{"start": 0, "end": 1, "text": "a"}])

        record = self.store.get(task_id)
        self.assertIsNotNone(record, "la tâche terminée doit produire une entrée")
        self.assertEqual(record["title"], "Ma vidéo")
        self.assertEqual(record["platform"], "youtube")
        self.assertEqual(record["lang_src"], "fr")
        self.assertEqual(record["lang_dst"], "en")
        self.assertEqual(len(record["segments"]), 1, "les segments servent à réexporter les sous-titres")

    def test_temp_is_emptied(self):
        """temp/ doit redevenir jetable : plus rien ne doit y rester."""
        task_id = self._completed_task()
        self.main._archive_task_to_library(task_id)
        leftovers = [p.name for p in self.temp_dir.iterdir()]
        self.assertEqual(leftovers, [], f"fichiers laissés dans temp/ : {leftovers}")

    def test_all_asset_kinds_archived(self):
        task_id = self._completed_task()
        self.main._archive_task_to_library(task_id)
        for kind in ("audio", "srt", "vtt", "raw", "script"):
            self.assertIsNotNone(self.store.asset_path(task_id, kind), f"produit manquant : {kind}")

    def test_media_size_is_the_media_not_the_entry(self):
        task_id = self._completed_task()
        self.main._archive_task_to_library(task_id)
        self.assertEqual(self.store.get(task_id)["media_size_bytes"], 4000)

    def test_legacy_urls_still_resolve_after_archiving(self):
        """L'interface d'avant adresse les fichiers par leur nom dans temp/."""
        task_id = self._completed_task()
        name = self.main.tasks[task_id]["media_filename"]
        self.main._archive_task_to_library(task_id)

        self.assertFalse((self.temp_dir / name).exists(), "le fichier a bien quitté temp/")
        resolved = self.main._resolve_temp_file(name, frozenset({".m4a"}))
        self.assertTrue(resolved.is_file(), "l'ancienne URL doit retomber sur la bibliothèque")

    def test_upload_without_url_is_labelled_upload(self):
        task_id = "t-upload"
        self.main.tasks[task_id] = {
            "status": "completed", "video_title": "Fichier local",
            "created_at": time.time(), "url": "mon-fichier.mp4", "script": "x",
        }
        self.main._archive_task_to_library(task_id)
        record = self.store.get(task_id)
        self.assertEqual(record["platform"], "upload")
        self.assertEqual(record["source_url"], "", "un nom de fichier n'est pas une URL")


class TestNoSpeechCompletion(ArchiveTestCase):
    """REGRESSION — le second point d'archivage, celui du court-circuit sans parole.

    Cas réel : un clic sur le bouton micro produit un enregistrement muet. Il doit
    quand même donner une entrée consultable, sinon l'utilisateur voit son
    enregistrement disparaître sans explication.
    """

    def test_silent_recording_still_produces_an_entry(self):
        task_id = "t-silence"
        self.main.tasks[task_id] = {
            "status": "completed",
            "no_speech": True,
            "video_title": "enregistrement-2026-08-30-20-46",
            "url": "enregistrement.webm",
            "created_at": time.time(),
            "detected_language": "",
            "summary_language": "fr",
            "script": "# enregistrement\n\nNo speech detected in this video",
            "summary": "",
            "media_filename": self.write_temp("upload_enregistrement.webm", 46000),
            "media_size_bytes": 46000,
        }
        self.main._archive_task_to_library(task_id, segments=None)

        record = self.store.get(task_id)
        self.assertIsNotNone(record, "un enregistrement muet doit rester consultable")
        self.assertTrue(record["no_speech"], "l'interface s'appuie dessus pour masquer le résumé")
        self.assertEqual(record["summary"], "")
        # Le type est donné par l'extension : .webm est classé « video » par
        # media_kind(), comme partout ailleurs dans l'application. Ce qui compte
        # ici est que le fichier soit conservé, pas sous quel type il l'est.
        kept = [k for k in ("audio", "video") if self.store.asset_path(task_id, k)]
        self.assertEqual(kept, ["video"],
                         "l'enregistrement doit être conservé même sans parole")
        self.assertEqual(self.store.asset_path(task_id, "video").suffix, ".webm")

    def test_pipeline_archives_at_both_exits(self):
        """Les autres tests appellent l'archivage directement : ils ne verraient pas
        qu'on a oublié de l'appeler. Or c'est exactement l'oubli qui ferait
        disparaître les enregistrements muets. On vérifie donc les points d'appel
        eux-mêmes, dans la source de la pipeline.
        """
        import inspect

        source = inspect.getsource(self.main._run_post_extract_pipeline)
        calls = source.count("_archive_task_to_library")
        self.assertEqual(
            calls, 2,
            "la pipeline a deux sorties (normale et « aucune parole ») et chacune "
            f"doit archiver ; {calls} appel(s) trouvé(s)",
        )

    def test_entry_is_listed(self):
        task_id = "t-silence-2"
        self.main.tasks[task_id] = {
            "status": "completed", "no_speech": True, "video_title": "muet",
            "created_at": time.time(), "script": "vide", "url": "",
        }
        self.main._archive_task_to_library(task_id)
        self.assertEqual(self.store.list()[1], 1)


class TestArchiveRobustness(ArchiveTestCase):
    """L'utilisateur a déjà son résultat : l'archivage ne doit jamais faire échouer la tâche."""

    def test_unknown_task_is_a_noop(self):
        self.main._archive_task_to_library("jamais-vu")
        self.assertEqual(self.store.list()[1], 0)

    def test_missing_files_do_not_prevent_the_entry(self):
        task_id = "t-partiel"
        self.main.tasks[task_id] = {
            "status": "completed", "video_title": "Produits manquants",
            "created_at": time.time(), "script": "texte", "url": "",
            "media_filename": "fichier_jamais_ecrit.m4a",
            "srt_filename": "absent.srt",
        }
        self.main._archive_task_to_library(task_id)

        record = self.store.get(task_id)
        self.assertIsNotNone(record, "le texte doit être sauvé même sans les produits")
        self.assertEqual(record["script"], "texte")
        self.assertIsNone(self.store.asset_path(task_id, "audio"))

    def test_filename_from_another_directory_is_ignored(self):
        """Les noms de produits ne doivent jamais faire sortir de temp/."""
        outside = Path(self._tmp.name) / "hors_temp.m4a"
        outside.write_bytes(b"\0" * 100)
        task_id = "t-chemin"
        self.main.tasks[task_id] = {
            "status": "completed", "video_title": "Chemin détourné",
            "created_at": time.time(), "script": "x", "url": "",
            "media_filename": str(Path("..") / outside.name),
        }
        self.main._archive_task_to_library(task_id)
        self.assertIsNone(self.store.asset_path(task_id, "audio"))
        self.assertTrue(outside.exists(), "un fichier hors de temp/ ne doit pas être déplacé")


if __name__ == "__main__":
    unittest.main()
