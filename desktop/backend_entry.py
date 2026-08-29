"""Point d'entrée du backend pour la version bureau (PyInstaller).

- En mode gelé (exe), les ressources (static/, modules backend) sont dans _MEIPASS.
- Les données utilisateur (temp/, tasks.json, modèles Whisper) vont dans
  %LOCALAPPDATA%/AI-Video-Transcriber par défaut (surchargable via AVT_DATA_DIR).
- Electron passe AVT_PORT et AVT_FFMPEG_DIR ; l'exe reste utilisable seul.
"""
import multiprocessing
import os
import sys
from pathlib import Path


def resource_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def main() -> None:
    multiprocessing.freeze_support()

    res = resource_dir()

    # Static embarqué (spec PyInstaller copie static/ dans le bundle)
    os.environ.setdefault("AVT_STATIC_DIR", str(res / "static"))

    # Données utilisateur hors du dossier d'installation (Program Files = lecture seule)
    default_data = Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "AI-Video-Transcriber"
    data_dir = Path(os.environ.setdefault("AVT_DATA_DIR", str(default_data)))
    data_dir.mkdir(parents=True, exist_ok=True)

    # Cache modèles faster-whisper au même endroit (évite re-téléchargement par version)
    os.environ.setdefault("HF_HOME", str(data_dir / "models"))

    # ffmpeg embarqué : dossier passé par Electron, sinon bin/ à côté de l'exe
    ffmpeg_dir = os.getenv("AVT_FFMPEG_DIR") or str(Path(sys.executable).parent / "bin")
    if Path(ffmpeg_dir).exists():
        os.environ["PATH"] = ffmpeg_dir + os.pathsep + os.environ.get("PATH", "")

    # Mode fenêtré (console=False) : sys.stdout/stderr sont None et le
    # formatter par défaut d'uvicorn plante sur .isatty — on redirige vers un log.
    if sys.stdout is None or sys.stderr is None:
        log_file = open(data_dir / "backend.log", "a", encoding="utf-8", buffering=1)
        if sys.stdout is None:
            sys.stdout = log_file
        if sys.stderr is None:
            sys.stderr = log_file

    if not getattr(sys, "frozen", False):
        sys.path.insert(0, str(res / "backend"))

    import logging

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )

    import uvicorn
    from main import app  # noqa: E402 — résolu via pathex (spec) ou sys.path ci-dessus

    port = int(os.getenv("AVT_PORT", os.getenv("PORT", "8765")))
    # log_config=None : évite le dictConfig uvicorn (incompatible sans console)
    uvicorn.run(app, host="127.0.0.1", port=port, log_config=None)


if __name__ == "__main__":
    main()
