# -*- coding: utf-8 -*-
"""Tests du diagnostic d'échec de téléchargement (backend/download_hints.py).

Les messages réels de yt-dlp sont repris tels qu'ils sortent : un test écrit à
partir d'un message inventé ne prouverait rien le jour où l'utilisateur voit le vrai.

    python -m unittest discover -s tests
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from download_hints import explain, format_for_user  # noqa: E402


class TestRecognition(unittest.TestCase):
    """Messages réellement émis par yt-dlp, copiés depuis ses sorties."""

    def assert_reason(self, message, expected):
        got = explain(message)["reason"]
        self.assertEqual(got, expected, f"{message[:60]!r} → {got}, attendu {expected}")

    def test_login_required(self):
        self.assert_reason(
            "ERROR: [instagram] Requested content is not available, rate-limit reached or "
            "login required. Use --cookies-from-browser", "login_required")
        self.assert_reason(
            "ERROR: [twitter] NSFW tweet requires authentication.", "login_required")
        self.assert_reason(
            "Sign in to confirm you're not a bot. Use --cookies-from-browser", "login_required")

    def test_age_restricted(self):
        self.assert_reason(
            "ERROR: [youtube] Sign in to confirm your age. This video may be inappropriate "
            "for some users.", "age_restricted")

    def test_private(self):
        self.assert_reason("ERROR: [youtube] Private video. Sign in if you've been granted access",
                           "private")
        self.assert_reason("ERROR: [instagram] This account is private", "private")

    def test_geo_blocked(self):
        self.assert_reason(
            "ERROR: [youtube] The uploader has not made this video available in your country",
            "geo_blocked")

    def test_rate_limited(self):
        self.assert_reason("ERROR: Unable to download webpage: HTTP Error 429: Too Many Requests",
                           "rate_limited")

    def test_unavailable(self):
        self.assert_reason("ERROR: [youtube] Video unavailable. This video has been removed",
                           "unavailable")
        self.assert_reason("ERROR: Unable to download webpage: HTTP Error 404: Not Found",
                           "unavailable")

    def test_unsupported(self):
        self.assert_reason("ERROR: Unsupported URL: https://exemple.test/page", "unsupported")

    def test_extractor_outdated(self):
        """Le cas propre aux applications installées : la plateforme a changé,
        le module embarqué est figé."""
        self.assert_reason(
            "ERROR: [tiktok] Unable to extract webpage video data", "extractor_outdated")
        self.assert_reason(
            "ERROR: [youtube] Failed to extract any player response", "extractor_outdated")

    def test_ffmpeg(self):
        self.assert_reason("ERROR: ffprobe and ffmpeg not found. Please install", "ffmpeg_missing")

    def test_network(self):
        self.assert_reason("ERROR: Unable to download webpage: <urlopen error timed out>", "network")

    def test_unknown_keeps_the_original(self):
        info = explain("ERROR: quelque chose de totalement inattendu")
        self.assertEqual(info["reason"], "unknown")
        self.assertIn("inattendu", info["detail"],
                      "un message non reconnu doit rester consultable")


class TestPriority(unittest.TestCase):
    """L'ordre des règles compte : plusieurs motifs coexistent dans un même message."""

    def test_private_wins_over_login(self):
        """« Private video. Sign in… » contient les deux ; le vrai problème est
        que la vidéo est privée, pas qu'il faut se connecter."""
        self.assertEqual(
            explain("Private video. Sign in if you've been granted access to this video")["reason"],
            "private")

    def test_age_wins_over_login(self):
        self.assertEqual(
            explain("Sign in to confirm your age. This video may be inappropriate")["reason"],
            "age_restricted")


class TestOutputShape(unittest.TestCase):

    def test_all_fields_present(self):
        for key in ("reason", "message", "hint", "detail"):
            self.assertIn(key, explain("n'importe quoi"))

    def test_messages_are_in_french_and_actionable(self):
        for sample in ("login required", "geo restricted", "Video unavailable",
                       "Unsupported URL", "Unable to extract", "timed out"):
            info = explain(sample)
            self.assertTrue(info["message"].strip(), sample)
            self.assertTrue(info["hint"].strip(), f"chaque cause doit dire quoi faire : {sample}")
            self.assertNotIn("ERROR", info["message"])

    def test_accepts_an_exception_object(self):
        info = explain(RuntimeError("HTTP Error 429: Too Many Requests"))
        self.assertEqual(info["reason"], "rate_limited")

    def test_accepts_none(self):
        self.assertEqual(explain(None)["reason"], "unknown")

    def test_one_line_format(self):
        line = format_for_user("Unsupported URL: https://x.test")
        self.assertNotIn("\n", line)
        self.assertTrue(len(line) > 20)


if __name__ == "__main__":
    unittest.main()
