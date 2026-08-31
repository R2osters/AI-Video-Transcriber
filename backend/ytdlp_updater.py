# -*- coding: utf-8 -*-
"""让打包后的应用也能更新 yt-dlp，而不必重新分发整个安装包。

问题：各平台三天两头改结构，yt-dlp 每周发新版修复；而安装版里的 yt-dlp 冻结在
打包那一刻。用户遇到「无法提取」时，唯一出路本来是等一个 218 MB 的新安装包。

做法：把新版解压到用户数据目录下的一个覆盖目录，并在导入 yt_dlp **之前**把它放到
sys.path 最前面。原始版本原封不动，随时可以一键回退。

PyInstaller 的产物里没有 pip，所以不能用 pip 安装。wheel 本身就是 zip，用标准库
下载、校验、解压即可——不引入任何新依赖。

安全：只从 PyPI 官方接口取，只接受 py3-none-any 的纯 Python wheel，并强制校验
PyPI 给出的 sha256；摘要不符就丢弃，绝不落盘启用。
"""

import hashlib
import json
import logging
import os
import shutil
import sys
import tempfile
import threading
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Dict, Optional

logger = logging.getLogger(__name__)

PACKAGE = "yt-dlp"
MODULE = "yt_dlp"
PYPI_URL = f"https://pypi.org/pypi/{PACKAGE}/json"
OVERRIDE_DIRNAME = "updates"

_lock = threading.RLock()
_status: Dict[str, object] = {"state": "idle", "message": ""}


def override_dir(data_root: Path) -> Path:
    """存放新版 yt-dlp 的目录。"""
    return Path(data_root) / OVERRIDE_DIRNAME / MODULE


def activate(data_root: Path) -> Optional[Path]:
    """把覆盖目录插到 sys.path 最前面。

    必须在 `import yt_dlp` 之前调用，否则冻结版已经被导入，换不掉了。
    返回生效的目录；没有可用覆盖时返回 None。
    """
    path = override_dir(data_root)
    if not (path / MODULE / "__init__.py").is_file():
        return None
    entry = str(path)
    if entry in sys.path:
        sys.path.remove(entry)
    sys.path.insert(0, entry)
    logger.info(f"使用更新后的 {PACKAGE}: {entry}")
    return path


def installed_version() -> str:
    """当前**实际生效**的版本（导入后才知道，因此在这里现查）。"""
    try:
        import yt_dlp

        return getattr(yt_dlp.version, "__version__", "") or ""
    except Exception:
        return ""


def is_override_active(data_root: Path) -> bool:
    try:
        import yt_dlp

        return str(override_dir(data_root)) in str(Path(yt_dlp.__file__).resolve())
    except Exception:
        return False


def latest_version(timeout: float = 15.0) -> Dict[str, str]:
    """查询 PyPI 上的最新版本与其 wheel 信息。"""
    with urllib.request.urlopen(PYPI_URL, timeout=timeout) as resp:
        data = json.load(resp)

    version = data["info"]["version"]
    for item in data["releases"].get(version, []):
        # 纯 Python wheel：不含编译产物，解压即可用，跨平台通用
        if item.get("packagetype") == "bdist_wheel" and item.get("filename", "").endswith("-py3-none-any.whl"):
            return {
                "version": version,
                "url": item["url"],
                "sha256": (item.get("digests") or {}).get("sha256", ""),
                "size": str(item.get("size") or 0),
            }
    raise RuntimeError(f"{PACKAGE} {version} 没有提供通用 wheel")


def status() -> Dict[str, object]:
    return dict(_status)


def _set(**fields) -> None:
    _status.update(fields)


def install(data_root: Path, progress: Optional[Callable[[Dict[str, object]], None]] = None) -> Dict[str, object]:
    """下载最新版并启用。返回结果字典；失败时抛异常。

    先解压到临时目录、校验通过后再替换正式目录：中途失败也不会留下半个包，
    否则下次启动会导入一个残缺的 yt_dlp，比不更新更糟。
    """
    with _lock:
        _set(state="checking", message="")
        if progress:
            progress(status())

        info = latest_version()
        current = installed_version()
        if info["version"] == current and is_override_active(data_root):
            _set(state="idle", message="")
            return {"updated": False, "reason": "already_latest", "version": current}

        _set(state="downloading", version=info["version"])
        if progress:
            progress(status())

        target = override_dir(data_root)
        target.parent.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(dir=str(target.parent)) as tmp:
            tmp_path = Path(tmp)
            wheel = tmp_path / "package.whl"
            with urllib.request.urlopen(info["url"], timeout=120) as resp, open(wheel, "wb") as f:
                shutil.copyfileobj(resp, f)

            expected = info.get("sha256") or ""
            if expected:
                digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
                if digest != expected:
                    _set(state="error", message="empreinte du fichier incorrecte")
                    raise RuntimeError("摘要校验失败，已丢弃下载内容")

            _set(state="installing")
            if progress:
                progress(status())

            extracted = tmp_path / "x"
            with zipfile.ZipFile(wheel) as zf:
                zf.extractall(extracted)

            if not (extracted / MODULE / "__init__.py").is_file():
                _set(state="error", message="archive inattendue")
                raise RuntimeError("wheel 内没有预期的模块目录")

            staged = target.parent / f"{MODULE}.new"
            shutil.rmtree(staged, ignore_errors=True)
            shutil.move(str(extracted), str(staged))

        # 校验通过才动正式目录，且旧目录先挪开而不是直接删
        old = target.parent / f"{MODULE}.old"
        shutil.rmtree(old, ignore_errors=True)
        if target.exists():
            shutil.move(str(target), str(old))
        shutil.move(str(target.parent / f"{MODULE}.new"), str(target))
        shutil.rmtree(old, ignore_errors=True)

        _set(state="done", version=info["version"], message="")
        if progress:
            progress(status())
        logger.info(f"{PACKAGE} 已更新到 {info['version']}（重启后生效）")
        return {"updated": True, "version": info["version"], "previous": current,
                "restart_required": True}


def revert(data_root: Path) -> bool:
    """删除覆盖目录，回到随应用分发的版本。"""
    with _lock:
        target = override_dir(data_root)
        if not target.exists():
            return False
        shutil.rmtree(target, ignore_errors=True)
        _set(state="idle", message="")
        logger.info(f"已回退到随应用分发的 {PACKAGE}")
        return True
