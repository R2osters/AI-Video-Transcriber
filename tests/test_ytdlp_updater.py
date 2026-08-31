# -*- coding: utf-8 -*-
"""Tests de la mise à jour du module de téléchargement (backend/ytdlp_updater.py).

Aucun accès réseau : PyPI est simulé. Ce qui est vérifié ici, c'est le
comportement dont dépend la sécurité et l'intégrité de l'installation —
on télécharge et on exécutera du code, donc une empreinte qui ne correspond pas
doit faire échouer l'opération sans rien laisser derrière.

    python -m unittest discover -s tests
"""

import hashlib
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import ytdlp_updater as up  # noqa: E402


def make_wheel(version="9.9.9", with_module=True) -> bytes:
    """Un wheel minimal mais réaliste : c'est un simple zip."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        if with_module:
            zf.writestr("yt_dlp/__init__.py", f"__version__ = '{version}'\n")
            zf.writestr("yt_dlp/version.py", f"__version__ = '{version}'\n")
        zf.writestr(f"yt_dlp-{version}.dist-info/METADATA", f"Version: {version}\n")
    return buf.getvalue()


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False


class UpdaterTestCase(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="avt_up_")
        self.root = Path(self._tmp.name)
        self.wheel = make_wheel()
        self.digest = hashlib.sha256(self.wheel).hexdigest()
        self._real_urlopen = up.urllib.request.urlopen
        self._saved_path = list(sys.path)
        # activate() s'exécute au démarrage, avant tout import de yt_dlp. D'autres
        # fichiers de tests importent main (donc yt_dlp) : on rétablit les conditions
        # réelles, sinon la garde « déjà importé » se déclenche à juste titre.
        self._saved_modules = {n: m for n, m in sys.modules.items()
                               if n == "yt_dlp" or n.startswith("yt_dlp.")}
        for name in self._saved_modules:
            sys.modules.pop(name, None)

    def tearDown(self):
        up.urllib.request.urlopen = self._real_urlopen
        sys.path[:] = self._saved_path
        sys.modules.update(self._saved_modules)
        self._tmp.cleanup()

    def fake_pypi(self, wheel=None, sha=None, version="9.9.9"):
        """Remplace PyPI : métadonnées puis contenu du wheel."""
        payload = {
            "info": {"version": version},
            "releases": {version: [{
                "packagetype": "bdist_wheel",
                "filename": f"yt_dlp-{version}-py3-none-any.whl",
                "url": "https://exemple.test/paquet.whl",
                "digests": {"sha256": sha if sha is not None else self.digest},
                "size": len(wheel if wheel is not None else self.wheel),
            }]},
        }

        def urlopen(url, *a, **k):
            target = url if isinstance(url, str) else getattr(url, "full_url", "")
            if "pypi.org" in target:
                return FakeResponse(json.dumps(payload).encode())
            return FakeResponse(wheel if wheel is not None else self.wheel)

        up.urllib.request.urlopen = urlopen


class TestActivation(UpdaterTestCase):

    def test_no_override_means_no_change(self):
        self.assertIsNone(up.activate(self.root))
        self.assertEqual(sys.path, self._saved_path)

    def test_override_goes_first(self):
        """La version mise à jour doit primer sur celle livrée dans l'application."""
        target = up.override_dir(self.root)
        (target / "yt_dlp").mkdir(parents=True)
        (target / "yt_dlp" / "__init__.py").write_text("", encoding="utf-8")

        activated = up.activate(self.root)
        self.assertEqual(activated, target)
        self.assertEqual(sys.path[0], str(target), "elle doit être en tête de sys.path")

    def test_activation_is_idempotent(self):
        target = up.override_dir(self.root)
        (target / "yt_dlp").mkdir(parents=True)
        (target / "yt_dlp" / "__init__.py").write_text("", encoding="utf-8")
        up.activate(self.root)
        up.activate(self.root)
        self.assertEqual(sys.path.count(str(target)), 1, "pas d'entrée en double")

    def test_incomplete_override_is_ignored(self):
        """Un dossier présent mais sans le module ne doit pas être activé :
        mieux vaut la version d'origine qu'un paquet à moitié installé."""
        (up.override_dir(self.root) / "yt_dlp").mkdir(parents=True)
        self.assertIsNone(up.activate(self.root))


class TestActivationGuards(UpdaterTestCase):
    """Deux pièges où l'utilisateur croirait tourner sur une mise à jour sans que ce soit vrai."""

    def make_override(self, version="9.9.9"):
        target = up.override_dir(self.root) / "yt_dlp"
        target.mkdir(parents=True, exist_ok=True)
        (target / "__init__.py").write_text("", encoding="utf-8")
        (target / "version.py").write_text(f"__version__ = '{version}'\n", encoding="utf-8")
        return target

    def fake_bundled(self, version):
        """Simule le yt_dlp livré avec l'application."""
        import types

        mod = types.ModuleType("yt_dlp")
        ver = types.ModuleType("yt_dlp.version")
        ver.__version__ = version
        mod.version = ver
        sys.modules["yt_dlp"] = mod
        sys.modules["yt_dlp.version"] = ver

    def tearDown(self):
        for name in [n for n in list(sys.modules) if n == "yt_dlp" or n.startswith("yt_dlp.")]:
            sys.modules.pop(name, None)
        super().tearDown()

    def test_refuses_when_already_imported(self):
        """REGRESSION — si un réordonnancement des imports fait charger yt_dlp avant
        activate(), la surcharge ne peut plus prendre. Il faut le dire, pas faire semblant."""
        self.make_override()
        self.fake_bundled("2020.01.01")
        # yt_dlp est déjà dans sys.modules à cet instant
        self.assertIsNone(up.activate(self.root))
        self.assertEqual(up.status().get("override"), "too_late")
        self.assertNotIn(str(up.override_dir(self.root)), sys.path)

    def test_older_override_is_ignored(self):
        """REGRESSION — après une mise à jour de l'application, une surcharge plus
        ancienne ferait tourner l'utilisateur sur de vieux extracteurs sans le savoir."""
        self.make_override(version="2026.01.01")
        original = up._bundled_version
        up._bundled_version = lambda: "2026.08.19"
        try:
            self.assertIsNone(up.activate(self.root))
        finally:
            up._bundled_version = original
        st = up.status()
        self.assertEqual(st.get("override"), "ignored_older")
        self.assertEqual(st.get("override_version"), "2026.01.01")
        self.assertEqual(st.get("bundled_version"), "2026.08.19")

    def test_override_directory_is_not_deleted_when_ignored(self):
        """On n'efface jamais de données tout seul : l'utilisateur décide."""
        self.make_override(version="2026.01.01")
        original = up._bundled_version
        up._bundled_version = lambda: "2026.08.19"
        try:
            up.activate(self.root)
        finally:
            up._bundled_version = original
        self.assertTrue((up.override_dir(self.root) / "yt_dlp" / "version.py").is_file())

    def test_newer_override_is_used(self):
        self.make_override(version="2026.09.01")
        original = up._bundled_version
        up._bundled_version = lambda: "2026.08.19"
        try:
            self.assertIsNotNone(up.activate(self.root))
        finally:
            up._bundled_version = original
        self.assertEqual(up.status().get("override"), "active")

    def test_equal_versions_prefer_the_bundle(self):
        """À version égale, autant utiliser celle de l'application : un fichier de
        moins à faire vivre."""
        self.make_override(version="2026.08.19")
        original = up._bundled_version
        up._bundled_version = lambda: "2026.08.19"
        try:
            self.assertIsNone(up.activate(self.root))
        finally:
            up._bundled_version = original

    def test_unreadable_version_does_not_block_activation(self):
        """Version illisible : on active plutôt que de bloquer — l'utilisateur a
        demandé cette mise à jour explicitement."""
        target = self.make_override()
        (target / "version.py").unlink()
        original = up._bundled_version
        up._bundled_version = lambda: "2026.08.19"
        try:
            self.assertIsNotNone(up.activate(self.root))
        finally:
            up._bundled_version = original


class TestVersionParsing(unittest.TestCase):

    def test_ordering(self):
        self.assertLess(up._parse_version("2026.01.01"), up._parse_version("2026.08.19"))
        self.assertLess(up._parse_version("2025.12.31"), up._parse_version("2026.01.01"))
        self.assertEqual(up._parse_version("2026.08.19"), up._parse_version("2026.08.19"))

    def test_suffixed_version(self):
        """yt-dlp publie parfois des versions suffixées."""
        self.assertEqual(up._parse_version("2026.08.19.1")[:3], (2026, 8, 19))

    def test_garbage_is_empty(self):
        self.assertEqual(up._parse_version("inconnue"), ())
        self.assertEqual(up._parse_version(""), ())


class TestInstall(UpdaterTestCase):

    def test_successful_install(self):
        self.fake_pypi()
        result = up.install(self.root)
        self.assertTrue(result["updated"])
        self.assertEqual(result["version"], "9.9.9")
        self.assertTrue(result["restart_required"], "le module déjà importé ne change pas à chaud")
        self.assertTrue((up.override_dir(self.root) / "yt_dlp" / "__init__.py").is_file())

    def test_installed_version_is_usable(self):
        self.fake_pypi()
        up.install(self.root)
        content = (up.override_dir(self.root) / "yt_dlp" / "version.py").read_text(encoding="utf-8")
        self.assertIn("9.9.9", content)

    def test_bad_digest_is_refused(self):
        """SÉCURITÉ — on télécharge du code qui sera exécuté au prochain démarrage.
        Une empreinte qui ne correspond pas doit tout faire échouer."""
        self.fake_pypi(sha="0" * 64)
        with self.assertRaises(RuntimeError):
            up.install(self.root)
        self.assertFalse(up.override_dir(self.root).exists(),
                         "rien ne doit être installé quand l'empreinte est fausse")

    def test_tampered_content_is_refused(self):
        """Même scénario, vu de l'autre côté : l'empreinte annoncée est correcte
        mais le contenu servi a été remplacé."""
        self.fake_pypi(wheel=make_wheel(version="6.6.6"))   # digest = celui du wheel d'origine
        with self.assertRaises(RuntimeError):
            up.install(self.root)
        self.assertFalse(up.override_dir(self.root).exists())

    def test_archive_without_module_is_refused(self):
        bogus = make_wheel(with_module=False)
        self.fake_pypi(wheel=bogus, sha=hashlib.sha256(bogus).hexdigest())
        with self.assertRaises(RuntimeError):
            up.install(self.root)
        self.assertFalse(up.override_dir(self.root).exists())

    def test_failure_keeps_the_previous_version(self):
        """Une mise à jour ratée ne doit pas casser une mise à jour réussie
        précédente : sinon l'application repart sur un module absent."""
        self.fake_pypi()
        up.install(self.root)
        marker = up.override_dir(self.root) / "yt_dlp" / "marqueur.txt"
        marker.write_text("version precedente", encoding="utf-8")

        self.fake_pypi(sha="0" * 64, version="9.9.10")
        with self.assertRaises(RuntimeError):
            up.install(self.root)
        self.assertTrue(marker.is_file(), "la version précédente doit rester intacte")

    def test_no_leftover_staging_directories(self):
        self.fake_pypi()
        up.install(self.root)
        leftovers = [p.name for p in up.override_dir(self.root).parent.iterdir()
                     if p.name.endswith((".new", ".old"))]
        self.assertEqual(leftovers, [], f"dossiers temporaires laissés : {leftovers}")


class TestRevert(UpdaterTestCase):

    def test_revert_removes_the_override(self):
        self.fake_pypi()
        up.install(self.root)
        self.assertTrue(up.revert(self.root))
        self.assertFalse(up.override_dir(self.root).exists())

    def test_revert_without_override(self):
        self.assertFalse(up.revert(self.root))


class TestPypiParsing(UpdaterTestCase):

    def test_only_universal_wheels_are_accepted(self):
        """Un wheel compilé pour une plateforme précise n'est pas utilisable ici."""
        payload = {
            "info": {"version": "1.2.3"},
            "releases": {"1.2.3": [
                {"packagetype": "sdist", "filename": "yt_dlp-1.2.3.tar.gz", "url": "x"},
                {"packagetype": "bdist_wheel", "filename": "yt_dlp-1.2.3-cp39-win_amd64.whl", "url": "y"},
            ]},
        }
        up.urllib.request.urlopen = lambda *a, **k: FakeResponse(json.dumps(payload).encode())
        with self.assertRaises(RuntimeError):
            up.latest_version()

    def test_reads_version_and_digest(self):
        self.fake_pypi()
        info = up.latest_version()
        self.assertEqual(info["version"], "9.9.9")
        self.assertEqual(info["sha256"], self.digest)


if __name__ == "__main__":
    unittest.main()
