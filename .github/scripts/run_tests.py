#!/usr/bin/env python3
"""Lance la suite de tests et refuse les esquives silencieuses.

`python -m unittest` sort avec le code 0 quand des tests sont ignorés. Or
tests/test_archive.py se déclare « skipped » si backend/main.py n'est pas
importable : sans ce garde-fou, une CI à qui il manque une dépendance
afficherait du vert sans avoir vérifié l'archivage — précisément le parcours
qui décide si les transcriptions de l'utilisateur sont conservées ou perdues.

Les esquives légitimes restent tolérées : certains cas ne valent que sous
Windows, là où le système refuse de supprimer un fichier encore ouvert.
"""
import sys
import unittest
from pathlib import Path

# Un skip dont le motif contient l'un de ces fragments est une panne
# d'environnement déguisée, pas un cas hors-plateforme.
MOTIFS_INTERDITS = ("non importable", "not importable", "ModuleNotFoundError")

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    # Même découverte que `python -m unittest discover -s tests` : tests/ n'est
    # pas un paquet, donc pas de top_level_dir. Un chemin peut être passé en
    # argument, ce qui permet d'éprouver le garde-fou lui-même sur un dossier
    # de test jetable.
    depart = sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "tests")
    suite = unittest.defaultTestLoader.discover(depart)
    resultat = unittest.TextTestRunner(verbosity=2).run(suite)

    if resultat.skipped:
        print(f"\n{len(resultat.skipped)} test(s) ignoré(s) :")
        for test, motif in resultat.skipped:
            print(f"  - {test} -> {motif}")

    fatals = [
        (test, motif)
        for test, motif in resultat.skipped
        if any(m in motif for m in MOTIFS_INTERDITS)
    ]
    if fatals:
        print(
            "\n::error::Des tests ont été ignorés parce que l'environnement est "
            "incomplet, pas parce qu'ils ne s'appliquaient pas. Une suite verte "
            "qui n'a rien exécuté ne prouve rien."
        )
        for test, motif in fatals:
            print(f"::error::{test} -> {motif}")
        return 1

    if not resultat.wasSuccessful():
        return 1

    joues = resultat.testsRun - len(resultat.skipped)
    print(f"\n{joues} test(s) réellement exécuté(s) sur {resultat.testsRun}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
