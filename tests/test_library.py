# -*- coding: utf-8 -*-
"""转录库（backend/library.py）的回归测试。

只用标准库，不需要安装任何东西，也不需要打桩 faster_whisper：
    python -m unittest discover -s tests

覆盖的不变量：
  - 索引与磁盘保持一致（entry.json 是权威数据源）
  - 删除只承认真正删掉的东西，绝不谎报
  - 结构版本可迁移，且不会破坏更高版本写出的库

带 REGRESSION 标记的用例对应实际发生过的缺陷，删掉它们等于让缺陷回来。
"""

import json
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from library import SCHEMA_VERSION, LibraryStore  # noqa: E402


class LibraryTestCase(unittest.TestCase):
    """带临时库与临时“temp 目录”的基类。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="avt_test_")
        self.root = Path(self._tmp.name)
        self.incoming = self.root / "temp"      # 模拟待入库的 temp 目录
        self.incoming.mkdir()
        self.store = LibraryStore(self.root / "library")

    def tearDown(self):
        self.store.close()
        self._tmp.cleanup()

    def make_asset(self, name: str, size: int = 1000) -> Path:
        path = self.incoming / name
        path.write_bytes(b"\0" * size)
        return path

    def add_entry(self, entry_id="e1", title="Titre", **assets_sizes):
        assets = {kind: self.make_asset(f"src_{kind}_{entry_id}.bin", size)
                  for kind, size in assets_sizes.items()}
        return self.store.add({"title": title, "script": "contenu"},
                              assets=assets, entry_id=entry_id)


class TestAdd(LibraryTestCase):

    def test_assets_are_moved_not_copied(self):
        """产物必须从 temp 移走：temp 随时可能被清空。"""
        src = self.make_asset("media.m4a", 5000)
        self.store.add({"title": "Vidéo"}, assets={"audio": src}, entry_id="a1")
        self.assertFalse(src.exists(), "le fichier source doit avoir été déplacé")
        self.assertTrue(self.store.asset_path("a1", "audio").is_file())

    def test_entry_json_holds_full_text(self):
        self.store.add({"title": "T", "script": "texte long", "summary": "résumé"}, entry_id="a2")
        record = json.loads((self.root / "library" / "a2" / "entry.json").read_text(encoding="utf-8"))
        self.assertEqual(record["script"], "texte long")
        self.assertEqual(record["summary"], "résumé")

    def test_flags_reflect_content(self):
        item = self.store.add(
            {"title": "T", "summary": "x", "translation": "y"},
            assets={"audio": self.make_asset("a.m4a"), "srt": self.make_asset("s.srt")},
            entry_id="a3",
        )
        self.assertTrue(item["has_audio"])
        self.assertTrue(item["has_subtitles"])
        self.assertTrue(item["has_summary"])
        self.assertTrue(item["has_translation"])

    def test_duplicate_id_gets_a_new_one(self):
        self.store.add({"title": "A"}, entry_id="dup")
        second = self.store.add({"title": "B"}, entry_id="dup")
        self.assertNotEqual(second["id"], "dup")
        self.assertEqual(self.store.list()[1], 2)

    def test_missing_asset_is_skipped_not_fatal(self):
        item = self.store.add({"title": "T"},
                              assets={"audio": self.incoming / "jamais_créé.m4a"},
                              entry_id="a4")
        self.assertFalse(item["has_audio"])

    def test_accented_and_spaced_path(self):
        """Reproduit C:\\Users\\Gédéon\\AppData\\Local\\AI Video Transcriber."""
        odd = self.root / "Gédéon" / "AI Video Transcriber" / "library"
        store = LibraryStore(odd)
        store.add({"title": "é€中文"}, entry_id="x1")
        self.assertEqual(store.get("x1")["title"], "é€中文")
        store.close()


class TestListing(LibraryTestCase):

    def setUp(self):
        super().setUp()
        now = time.time()
        for i, (eid, title, fav) in enumerate([
            ("l1", "Conférence climat", False),
            ("l2", "Réunion produit", True),
            ("l3", "机器学习教程", False),
        ]):
            self.store.add({"title": title, "created_at": now - i * 3600, "favorite": fav},
                           entry_id=eid)

    def test_sorted_newest_first(self):
        items, total = self.store.list()
        self.assertEqual(total, 3)
        self.assertEqual([i["id"] for i in items], ["l1", "l2", "l3"])

    def test_search_is_case_insensitive_on_title(self):
        items, total = self.store.list(q="CONFÉRENCE")
        self.assertEqual(total, 1)
        self.assertEqual(items[0]["id"], "l1")

    def test_search_matches_non_latin(self):
        self.assertEqual(self.store.list(q="机器")[1], 1)

    def test_pagination_reports_full_total(self):
        items, total = self.store.list(limit=2, offset=1)
        self.assertEqual(total, 3, "le total doit ignorer la pagination")
        self.assertEqual(len(items), 2)

    def test_favorites_filter(self):
        items, total = self.store.list(favorites_only=True)
        self.assertEqual(total, 1)
        self.assertEqual(items[0]["id"], "l2")


class TestAssetAccess(LibraryTestCase):

    def setUp(self):
        super().setUp()
        self.add_entry("s1", audio=2000, srt=100)

    def test_known_kinds_resolve(self):
        self.assertEqual(self.store.asset_path("s1", "audio").name, "audio.bin")
        self.assertEqual(self.store.asset_path("s1", "srt").name, "subs.bin")

    def test_absent_asset_returns_none(self):
        self.assertIsNone(self.store.asset_path("s1", "video"))

    def test_unknown_kind_returns_none(self):
        self.assertIsNone(self.store.asset_path("s1", "../../etc/passwd"))

    def test_path_traversal_id_is_refused(self):
        """L'identifiant ne doit jamais servir à composer un chemin librement."""
        for bad in ("../../etc", "..", "a/b", "a\\b", ""):
            self.assertIsNone(self.store.asset_path(bad, "audio"), f"id accepté à tort: {bad!r}")

    def test_legacy_filename_mapping(self):
        """Les anciennes URL /api/media/{nom} doivent continuer de résoudre."""
        located = self.store.find_by_filename("src_audio_s1.bin")
        self.assertEqual(located, ("s1", "audio"))
        self.assertIsNone(self.store.find_by_filename("inconnu.mp3"))

    def test_mapping_survives_index_loss(self):
        """entry.json fait foi : l'index doit pouvoir être reconstruit."""
        self.store.close()
        for db_file in (self.root / "library").glob("index.db*"):
            db_file.unlink()
        store = LibraryStore(self.root / "library")
        store.reindex()
        self.assertEqual(store.find_by_filename("src_srt_s1.bin"), ("s1", "srt"))
        store.close()


class TestUpdateAndDelete(LibraryTestCase):

    def test_rename_updates_index_and_disk(self):
        self.add_entry("u1", title="Avant")
        self.store.update("u1", title="Après")
        self.assertEqual(self.store.get_meta("u1")["title"], "Après")
        record = json.loads((self.root / "library" / "u1" / "entry.json").read_text(encoding="utf-8"))
        self.assertEqual(record["title"], "Après", "entry.json doit rester la source de vérité")

    def test_favorite_toggle(self):
        self.add_entry("u2")
        self.assertTrue(self.store.update("u2", favorite=True)["favorite"])
        self.assertFalse(self.store.update("u2", favorite=False)["favorite"])

    def test_update_unknown_returns_none(self):
        self.assertIsNone(self.store.update("jamais-vu", title="X"))

    def test_delete_removes_row_and_directory(self):
        self.add_entry("d1", audio=3000)
        self.assertTrue(self.store.delete("d1"))
        self.assertFalse((self.root / "library" / "d1").exists())
        self.assertIsNone(self.store.get_meta("d1"))
        self.assertFalse(self.store.delete("d1"), "une 2e suppression doit être un échec franc")

    def test_delete_clears_filename_mapping(self):
        self.add_entry("d2", audio=1000)
        self.store.delete("d2")
        self.assertIsNone(self.store.find_by_filename("src_audio_d2.bin"))


class TestLockedFileHandling(LibraryTestCase):
    """REGRESSION — sous Windows un fichier encore ouvert ne peut pas être supprimé.

    Les deux défauts corrigés venaient de là : une suppression qui se déclarait
    réussie en laissant l'audio sur le disque, et un décompte d'espace libéré
    qui annonçait des octets jamais rendus.
    """

    def test_delete_hides_entry_even_if_file_is_locked(self):
        self.add_entry("k1", audio=4000)
        locked = self.store.asset_path("k1", "audio")
        with open(locked, "rb"):
            self.assertTrue(self.store.delete("k1"))
            # L'entrée doit disparaître de l'interface immédiatement…
            self.assertIsNone(self.store.get_meta("k1"))
            self.assertEqual(self.store.list()[1], 0)

    def test_orphan_directory_is_purged_on_next_start(self):
        """REGRESSION : l'audio restait sur le disque à jamais, invisible."""
        self.add_entry("k2", audio=4000)
        entry_dir = self.root / "library" / "k2"
        locked = self.store.asset_path("k2", "audio")

        with open(locked, "rb"):
            self.store.delete("k2")
            still_there = entry_dir.exists()

        if not still_there:
            self.skipTest("le système autorise la suppression d'un fichier ouvert")

        # Redémarrage : plus aucun verrou, le résidu doit partir
        self.store.close()
        self.store = LibraryStore(self.root / "library")
        result = self.store.reindex()
        self.assertFalse(entry_dir.exists(), "le dossier résiduel doit être nettoyé")
        self.assertEqual(result["purged"], 1)

    def test_freed_bytes_are_never_overstated(self):
        """REGRESSION : la taille était comptée avant la suppression du fichier."""
        self.add_entry("k3", audio=7000)
        locked = self.store.asset_path("k3", "audio")

        with open(locked, "rb"):
            freed = self.store.drop_assets("k3")
            if not locked.exists():
                self.skipTest("le système autorise la suppression d'un fichier ouvert")
            self.assertEqual(freed, 0, "n'annoncer que ce qui a réellement été libéré")
            self.assertTrue(self.store.get_meta("k3")["has_audio"],
                            "l'entrée doit garder la référence tant que le fichier est là")

        # Verrou levé : la nouvelle tentative aboutit et annonce le vrai chiffre
        self.assertEqual(self.store.drop_assets("k3"), 7000)

    def test_incomplete_archive_directory_is_purged(self):
        """Un dossier sans entry.json ne peut être qu'un résidu."""
        debris = self.root / "library" / "abandonne"
        debris.mkdir()
        (debris / "audio.m4a").write_bytes(b"\0" * 100)
        self.assertEqual(self.store.reindex()["purged"], 1)
        self.assertFalse(debris.exists())


class TestFreeSpace(LibraryTestCase):

    def test_drop_assets_keeps_texts(self):
        self.add_entry("f1", audio=9000)
        freed = self.store.drop_assets("f1")
        self.assertEqual(freed, 9000)
        self.assertIsNotNone(self.store.get("f1"), "le texte doit survivre")
        self.assertFalse(self.store.get_meta("f1")["has_audio"])

    def test_purge_candidates_spares_favorites(self):
        old = time.time() - 90 * 86400
        self.store.add({"title": "Vieux", "created_at": old},
                       assets={"audio": self.make_asset("v.m4a")}, entry_id="p1")
        self.store.add({"title": "Vieux favori", "created_at": old, "favorite": True},
                       assets={"audio": self.make_asset("f.m4a")}, entry_id="p2")
        ids = [c["id"] for c in self.store.purge_candidates(older_than_days=30)]
        self.assertEqual(ids, ["p1"])

    def test_purge_candidates_ignores_entries_without_media(self):
        self.store.add({"title": "Texte seul", "created_at": time.time() - 90 * 86400}, entry_id="p3")
        self.assertEqual(self.store.purge_candidates(older_than_days=30), [])


class TestNoAutomaticDeletion(LibraryTestCase):
    """Décision produit verrouillée : rien ne disparaît sans action de l'utilisateur."""

    def test_gc_is_inert_without_explicit_limits(self):
        self.add_entry("g1", audio=5000)
        result = self.store.gc()
        self.assertTrue(result["skipped"])
        self.assertEqual(result["removed"], 0)
        self.assertIsNotNone(self.store.get_meta("g1"))

    def test_gc_acts_only_when_asked(self):
        self.store.add({"title": "Vieux", "created_at": time.time() - 100 * 86400}, entry_id="g2")
        self.assertEqual(self.store.gc(max_age_days=30)["removed"], 1)

    def test_gc_spares_favorites(self):
        self.store.add({"title": "Vieux favori", "created_at": time.time() - 100 * 86400,
                        "favorite": True}, entry_id="g3")
        self.assertEqual(self.store.gc(max_age_days=30)["removed"], 0)


class TestSchemaVersioning(LibraryTestCase):
    """La base d'un utilisateur installé survit aux mises à jour de l'exécutable."""

    def _set_version(self, value):
        self.store.close()
        db = sqlite3.connect(str(self.root / "library" / "index.db"))
        db.execute(f"PRAGMA user_version = {value}")
        db.commit()
        db.close()

    def test_fresh_library_is_stamped(self):
        version = self.store._db.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(version, SCHEMA_VERSION)

    def test_unversioned_library_is_migrated(self):
        self.add_entry("v1")
        self._set_version(0)
        self.store = LibraryStore(self.root / "library")
        self.assertEqual(self.store._db.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
        self.assertIsNotNone(self.store.get("v1"), "les données doivent survivre à la migration")

    def test_newer_library_is_not_damaged(self):
        """Installer une version plus ancienne ne doit pas casser la bibliothèque."""
        self.add_entry("v2")
        self._set_version(SCHEMA_VERSION + 50)
        self.store = LibraryStore(self.root / "library")
        self.assertIsNotNone(self.store.get("v2"))
        self.assertEqual(self.store._db.execute("PRAGMA user_version").fetchone()[0],
                         SCHEMA_VERSION + 50, "la version supérieure doit être laissée intacte")


class TestReindex(LibraryTestCase):

    def test_missing_directory_is_dropped_from_index(self):
        import shutil
        self.add_entry("r1")
        shutil.rmtree(self.root / "library" / "r1")
        self.assertEqual(self.store.reindex()["dropped"], 1)
        self.assertEqual(self.store.list()[1], 0)

    def test_entry_is_recovered_from_disk(self):
        self.add_entry("r2", title="Récupérable")
        self.store.close()
        for db_file in (self.root / "library").glob("index.db*"):
            db_file.unlink()
        self.store = LibraryStore(self.root / "library")
        self.assertEqual(self.store.reindex()["added"], 1)
        self.assertEqual(self.store.get_meta("r2")["title"], "Récupérable")


class TestImport(LibraryTestCase):
    """Reprise de l'ancien historique du navigateur."""

    def test_import_counts_and_keeps_text(self):
        count = self.store.import_entries([
            {"id": "old-1", "title": "Migré", "script": "abc"},
            {"title": "Sans identifiant"},
        ])
        self.assertEqual(count, 2)
        self.assertEqual(self.store.get("old-1")["script"], "abc")

    def test_import_survives_a_bad_entry(self):
        count = self.store.import_entries([{"title": "Bon"}, None, {"title": "Aussi bon"}])
        self.assertEqual(count, 2, "une entrée invalide ne doit pas interrompre l'import")


if __name__ == "__main__":
    unittest.main()
