"""Point d'entrée du backend pour la version bureau (PyInstaller).

- En mode gelé (exe), les ressources (static/, modules backend) sont dans _MEIPASS.
- Les données utilisateur (temp/, library/, modèles Whisper) vont dans
  %LOCALAPPDATA%/AI-Video-Transcriber par défaut (surchargable via AVT_DATA_DIR).
- Electron passe AVT_PORT, AVT_DATA_DIR et AVT_FFMPEG_DIR ; l'exe reste utilisable seul.
- AVT_SELFTEST=1 : vérifie le bundle (sqlite3, bibliothèque, app FastAPI) puis sort.
  Utilisé par scripts/build-backend.ps1 pour valider le build, pas au runtime.
"""
import json
import multiprocessing
import os
import sys
import threading
from pathlib import Path

# Modèle préchargé au premier lancement (vide = pas de préchargement).
DEFAULT_PRELOAD_MODEL = "base"

# Fichier d'état lu par l'écran de démarrage Electron (desktop/splash.html).
STATUS_FILENAME = "engine-status.json"


def resource_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def write_status(data_dir: Path, **fields) -> None:
    """Écrit l'état du moteur pour l'écran de démarrage (écriture atomique)."""
    try:
        tmp = data_dir / (STATUS_FILENAME + ".tmp")
        tmp.write_text(json.dumps(fields, ensure_ascii=False), encoding="utf-8")
        tmp.replace(data_dir / STATUS_FILENAME)
    except Exception:
        # L'état n'est qu'un confort d'affichage : jamais bloquant.
        pass


def model_is_cached(model_size: str) -> bool:
    """Vrai si le modèle faster-whisper est déjà dans le cache Hugging Face."""
    hub = Path(os.environ.get("HF_HOME", "")) / "hub"
    if not hub.is_dir():
        return False
    repo = hub / f"models--Systran--faster-whisper-{model_size}"
    snapshots = repo / "snapshots"
    if not snapshots.is_dir():
        return False
    # Un snapshot complet contient le modèle CTranslate2.
    return any(snap.glob("model.bin") for snap in snapshots.iterdir() if snap.is_dir())


def preload_model(data_dir: Path, model_size: str) -> None:
    """Télécharge le modèle Whisper par défaut en tâche de fond.

    Le serveur répond déjà pendant ce temps : l'écran de démarrage suit l'état
    via engine-status.json et laisse l'utilisateur continuer sans attendre.
    """
    import logging

    logger = logging.getLogger("preload")
    try:
        if model_is_cached(model_size):
            write_status(data_dir, state="ready", model=model_size, cached=True)
            return

        write_status(data_dir, state="downloading", model=model_size)
        logger.info("Préchargement du modèle Whisper %s...", model_size)

        from faster_whisper import WhisperModel

        # Mêmes paramètres que backend/transcriber.py : on remplit le même cache.
        model = WhisperModel(model_size, device="cpu", compute_type="int8")
        del model  # libère la RAM : on ne voulait que le téléchargement

        write_status(data_dir, state="ready", model=model_size, cached=False)
        logger.info("Modèle Whisper %s prêt.", model_size)
    except Exception as exc:  # réseau coupé, disque plein, modèle indisponible
        logger.warning("Préchargement du modèle %s impossible : %s", model_size, exc)
        write_status(data_dir, state="error", model=model_size, message=str(exc))


def selftest() -> int:
    """Vérifie que le bundle contient bien tout ce dont le runtime a besoin."""
    checks = []

    def check(label, fn, optional=False):
        """optional=True : un module encore absent des sources est ignoré, mais
        un module présent qui casse au chargement reste une erreur."""
        try:
            fn()
            checks.append((label, True, ""))
        except ModuleNotFoundError as exc:
            if optional:
                checks.append((label, True, f"absent (ignoré) : {exc}"))
            else:
                checks.append((label, False, str(exc)))
        except Exception as exc:
            checks.append((label, False, str(exc)))

    def _sqlite():
        import sqlite3

        con = sqlite3.connect(":memory:")
        con.execute("create table t (a integer)")
        con.execute("pragma journal_mode=wal")
        con.close()

    def _library():
        import library  # noqa: F401 — bibliothèque de transcriptions

    def _app():
        from main import app  # noqa: F401

    check("sqlite3 (+WAL)", _sqlite)
    # library.py arrive avec la bibliothèque de transcriptions ; tant qu'il n'est
    # pas mergé son absence n'est pas une erreur. S'il est là mais casse le
    # chargement, « backend.main:app » le signalera de toute façon (main l'importe).
    check("backend.library", _library, optional=True)
    check("backend.main:app", _app)
    check("faster_whisper", lambda: __import__("faster_whisper"))
    check("yt_dlp", lambda: __import__("yt_dlp"))

    ok = True
    for label, passed, detail in checks:
        suffix = f" -> {detail}" if detail else ""
        print(f"[{'OK ' if passed else 'ECHEC'}] {label}{suffix}")
        ok = ok and passed
    return 0 if ok else 1


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

    # Les logs du backend sont en chinois (dépôt amont). Sur une console Windows
    # en cp1252, logging lève UnicodeEncodeError et crache une trace ; on force
    # l'UTF-8 quand c'est possible (le fichier backend.log est déjà en UTF-8).
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

    import logging

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )

    if os.getenv("AVT_SELFTEST") == "1":
        sys.exit(selftest())

    import uvicorn
    from main import app  # noqa: E402 — résolu via pathex (spec) ou sys.path ci-dessus

    # Préchargement du modèle Whisper en tâche de fond (premier lancement).
    preload = os.getenv("AVT_PRELOAD_MODEL", DEFAULT_PRELOAD_MODEL).strip()
    if preload:
        write_status(data_dir, state="starting", model=preload)
        threading.Thread(
            target=preload_model, args=(data_dir, preload), name="preload", daemon=True
        ).start()
    else:
        write_status(data_dir, state="ready", model="", cached=True)

    port = int(os.getenv("AVT_PORT", os.getenv("PORT", "8765")))
    # log_config=None : évite le dictConfig uvicorn (incompatible sans console)
    uvicorn.run(app, host="127.0.0.1", port=port, log_config=None)


if __name__ == "__main__":
    main()
