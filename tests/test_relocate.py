# -*- coding: utf-8 -*-
"""转录库迁移的测试（backend/main.py 的 _relocate_library）。

这是搬动用户全部转录与原始音频的操作，出错的代价是数据丢失，因此这里做**真实**
的文件搬迁，只是数据很小。重点不在「能不能搬」，而在失败时原件是否还在。

    python -m unittest discover -s tests
"""

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def _install_stubs():
    if "faster_whisper" not in sys.modules:
        stub = types.ModuleType("faster_whisper")

        class WhisperModel:
            def __init__(self, *a, **k):
                pass

        stub.WhisperModel = WhisperModel
        sys.modules["faster_whisper"] = stub


class RelocateTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        import os

        _install_stubs()
        cls._boot = tempfile.TemporaryDirectory(prefix="avt_reloc_boot_")
        os.environ["AVT_DATA_DIR"] = cls._boot.name
        os.environ["AVT_STATIC_DIR"] = str(_ROOT / "static")
        os.environ.pop("AVT_TOKEN", None)
        sys.path.insert(0, str(_ROOT / "backend"))
        try:
            import main
        except Exception as exc:  # pragma: no cover
            raise unittest.SkipTest(f"backend/main.py non importable ici : {exc}")
        cls.main = main

    @classmethod
    def tearDownClass(cls):
        try:
            cls.main.library_store.close()
        except Exception:
            pass
        cls._boot.cleanup()

    def setUp(self):
        from library import LibraryStore

        self._tmp = tempfile.TemporaryDirectory(prefix="avt_reloc_")
        self.root = Path(self._tmp.name)
        self.source = self.root / "source"
        self.target = self.root / "cible"

        self._saved = (self.main.LIBRARY_DIR, self.main.library_store, self.main.CONFIG_FILE)
        self.main.CONFIG_FILE = self.root / "config.json"
        self.main.LIBRARY_DIR = self.source
        self.main.library_store = LibraryStore(self.source)
        self.main._relocation.update({"state": "idle", "copied_bytes": 0,
                                      "total_bytes": 0, "target": "", "error": None})

        for i in range(3):
            media = self.root / f"m{i}.m4a"
            media.write_bytes(b"\0" * 2048)
            self.main.library_store.add(
                {"title": f"Transcription {i}", "script": "contenu " * 20},
                assets={"audio": media}, entry_id=f"e{i}",
            )

    def tearDown(self):
        try:
            self.main.library_store.close()
        except Exception:
            pass
        self.main.LIBRARY_DIR, self.main.library_store, self.main.CONFIG_FILE = self._saved
        self._tmp.cleanup()


class TestSuccessfulRelocation(RelocateTestCase):

    def test_entries_and_files_arrive(self):
        self.main._relocate_library(self.target)
        self.assertEqual(self.main._relocation["state"], "done", self.main._relocation.get("error"))

        items, total = self.main.library_store.list()
        self.assertEqual(total, 3, "les trois entrées doivent être présentes après déplacement")
        self.assertEqual({i["title"] for i in items},
                         {"Transcription 0", "Transcription 1", "Transcription 2"})

    def test_audio_follows_the_entries(self):
        self.main._relocate_library(self.target)
        for i in range(3):
            path = self.main.library_store.asset_path(f"e{i}", "audio")
            self.assertIsNotNone(path, f"l'audio de e{i} doit avoir suivi")
            self.assertEqual(path.stat().st_size, 2048)
            self.assertTrue(str(path).startswith(str(self.target)), "il doit être au nouvel emplacement")

    def test_source_is_removed_only_after_success(self):
        self.main._relocate_library(self.target)
        self.assertFalse(self.source.exists(), "l'ancien emplacement est nettoyé une fois la copie vérifiée")

    def test_choice_is_persisted(self):
        self.main._relocate_library(self.target)
        cfg = json.loads(self.main.CONFIG_FILE.read_text(encoding="utf-8"))
        self.assertEqual(cfg["library_dir"], str(self.target),
                         "le nouvel emplacement doit survivre au redémarrage")

    def test_module_paths_are_updated(self):
        self.main._relocate_library(self.target)
        self.assertEqual(self.main.LIBRARY_DIR, self.target)
        self.assertEqual(Path(self.main.library_store.root), self.target)


class TestFailedRelocation(RelocateTestCase):
    """Le cas qui compte vraiment : quand ça rate, les données doivent rester."""

    def test_source_survives_a_failure(self):
        import shutil

        original_copy = shutil.copy2
        calls = {"n": 0}

        def exploding_copy(src, dst, *a, **k):
            calls["n"] += 1
            if calls["n"] > 2:
                raise OSError(28, "No space left on device")
            return original_copy(src, dst, *a, **k)

        shutil.copy2 = exploding_copy
        try:
            self.main._relocate_library(self.target)
        finally:
            shutil.copy2 = original_copy

        self.assertEqual(self.main._relocation["state"], "error")
        self.assertTrue(self.source.exists(), "l'ancien emplacement ne doit PAS avoir été supprimé")

        items, total = self.main.library_store.list()
        self.assertEqual(total, 3, "les entrées restent lisibles depuis l'ancien emplacement")
        for i in range(3):
            self.assertIsNotNone(self.main.library_store.asset_path(f"e{i}", "audio"))

    def test_config_is_not_written_on_failure(self):
        import shutil

        original_copy = shutil.copy2
        shutil.copy2 = lambda *a, **k: (_ for _ in ()).throw(OSError("disque plein"))
        try:
            self.main._relocate_library(self.target)
        finally:
            shutil.copy2 = original_copy

        self.assertFalse(self.main.CONFIG_FILE.exists(),
                         "un déplacement raté ne doit pas laisser une configuration qui pointe dans le vide")
        self.assertEqual(self.main.LIBRARY_DIR, self.source)


class TestConfigResolution(unittest.TestCase):
    """resolve_library_dir doit toujours rendre un dossier utilisable."""

    def setUp(self):
        _install_stubs()
        sys.path.insert(0, str(_ROOT / "backend"))
        import main

        self.main = main
        self._tmp = tempfile.TemporaryDirectory(prefix="avt_cfg_")
        self.root = Path(self._tmp.name)
        self._saved_cfg = main.CONFIG_FILE
        main.CONFIG_FILE = self.root / "config.json"

    def tearDown(self):
        self.main.CONFIG_FILE = self._saved_cfg
        self._tmp.cleanup()

    def test_default_when_no_config(self):
        self.assertEqual(self.main.resolve_library_dir(), self.main.DATA_ROOT / "library")

    def test_configured_path_is_used(self):
        wanted = self.root / "ailleurs"
        self.main.CONFIG_FILE.write_text(json.dumps({"library_dir": str(wanted)}), encoding="utf-8")
        self.assertEqual(self.main.resolve_library_dir(), wanted)
        self.assertTrue(wanted.is_dir(), "le dossier doit être créé s'il n'existe pas")

    def test_unusable_path_falls_back_instead_of_crashing(self):
        """Disque externe débranché : l'application doit démarrer quand même."""
        self.main.CONFIG_FILE.write_text(
            json.dumps({"library_dir": "Z:\\\\disque-absent\\\\library"}), encoding="utf-8")
        self.assertEqual(self.main.resolve_library_dir(), self.main.DATA_ROOT / "library")

    def test_corrupt_config_is_survivable(self):
        self.main.CONFIG_FILE.write_text("{ pas du json", encoding="utf-8")
        self.assertEqual(self.main.resolve_library_dir(), self.main.DATA_ROOT / "library")


if __name__ == "__main__":
    unittest.main()
