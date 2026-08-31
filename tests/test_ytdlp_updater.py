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

    def tearDown(self):
        up.urllib.request.urlopen = self._real_urlopen
        sys.path[:] = self._saved_path
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
