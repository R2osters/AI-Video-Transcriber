"""转录库（历史记录）持久化存储。

设计：
  - 每条记录一个目录 library/<id>/，内含 entry.json（元数据 + 全文）与产物文件
    （audio.*、subs.srt、subs.vtt、raw.md）。大文本不进数据库。
  - library/index.db（SQLite，WAL）只存可检索的元数据列，列表/搜索/统计走索引。
  - 默认不自动清理：gc() 需显式传入配额或天数才会删除任何内容。

本模块不依赖 main.py，路径由调用方注入，便于测试与桌面版覆盖数据目录。
"""

import json
import logging
import os
import re
import shutil
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 目录名即记录 id，必须严格校验，避免路径穿越
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# 产物类型 → 库内固定基名（扩展名在 add() 时按源文件推断）
ASSET_BASENAMES = {
    "audio": "audio",
    "video": "video",
    "srt": "subs",
    "vtt": "subs",
    "raw": "raw",
}
ASSET_KINDS = tuple(ASSET_BASENAMES)

# 索引结构版本。安装版（exe）用户的库会跨版本存活，
# 任何列变更都必须递增此值并在 _migrate() 中追加迁移分支。
SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id              TEXT PRIMARY KEY,
    created_at      REAL    NOT NULL,
    title           TEXT    NOT NULL DEFAULT '',
    platform        TEXT,
    source_url      TEXT,
    lang_src        TEXT,
    lang_dst        TEXT,
    model           TEXT,
    source_mode     TEXT,
    duration_ms     INTEGER NOT NULL DEFAULT 0,
    elapsed_ms      INTEGER NOT NULL DEFAULT 0,
    size_bytes      INTEGER NOT NULL DEFAULT 0,
    favorite        INTEGER NOT NULL DEFAULT 0,
    no_speech       INTEGER NOT NULL DEFAULT 0,
    has_audio       INTEGER NOT NULL DEFAULT 0,
    has_subtitles   INTEGER NOT NULL DEFAULT 0,
    has_summary     INTEGER NOT NULL DEFAULT 0,
    has_translation INTEGER NOT NULL DEFAULT 0,
    assets          TEXT    NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_entries_created ON entries(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_entries_favorite ON entries(favorite, created_at DESC);
"""

_LIST_COLUMNS = (
    "id", "created_at", "title", "platform", "source_url", "lang_src", "lang_dst",
    "model", "source_mode", "duration_ms", "elapsed_ms", "size_bytes", "favorite",
    "no_speech", "has_audio", "has_subtitles", "has_summary", "has_translation",
)


def new_id() -> str:
    """生成新的记录 id（任务 id 不可用时的兜底）。"""
    return uuid.uuid4().hex


def _dir_size(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            pass
    return total


class LibraryStore:
    """转录库：目录 + SQLite 索引。所有公开方法线程安全。"""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db_path = self.root / "index.db"
        self._db = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=NORMAL")
            self._db.executescript(_SCHEMA)
            self._db.commit()
            self._migrate()
        logger.info(f"转录库就绪: {self.root} (schema v{SCHEMA_VERSION})")

    def _migrate(self) -> None:
        """按 PRAGMA user_version 迁移索引结构。

        安装版用户升级 exe 后仍带着旧库：新版本只能就地迁移，不能重建。
        entry.json 是权威数据源，索引可随时用 reindex() 重建，因此
        遇到未来版本的库（降级安装）时保持只读而非损坏它。
        """
        current = self._db.execute("PRAGMA user_version").fetchone()[0]
        if current == SCHEMA_VERSION:
            return
        if current > SCHEMA_VERSION:
            logger.warning(
                f"转录库结构版本 v{current} 高于本程序 v{SCHEMA_VERSION}："
                "可能安装了旧版本，将按只读兼容方式使用"
            )
            return
        if current == 0:
            # 全新库，或 v1 之前无版本标记的库：结构已由 _SCHEMA 建好
            pass
        # 后续版本在此追加：
        # if current < 2: self._db.execute("ALTER TABLE entries ADD COLUMN ...")
        self._db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self._db.commit()
        if current:
            logger.info(f"转录库结构已迁移 v{current} → v{SCHEMA_VERSION}")

    # ── 内部工具 ──────────────────────────────────────────

    def _entry_dir(self, entry_id: str) -> Path:
        if not _SAFE_ID.match(entry_id or ""):
            raise ValueError(f"非法记录 id: {entry_id!r}")
        return self.root / entry_id

    @staticmethod
    def _row_to_item(row: sqlite3.Row) -> Dict[str, Any]:
        item = {k: row[k] for k in _LIST_COLUMNS}
        for flag in ("favorite", "no_speech", "has_audio", "has_subtitles",
                     "has_summary", "has_translation"):
            item[flag] = bool(item[flag])
        return item

    # ── 写入 ──────────────────────────────────────────────

    def add(
        self,
        entry: Dict[str, Any],
        assets: Optional[Dict[str, Path]] = None,
        entry_id: Optional[str] = None,
        move: bool = True,
    ) -> Dict[str, Any]:
        """新增一条记录。

        Args:
            entry: 元数据 + 全文（script/summary/translation/segments…），原样写入 entry.json
            assets: {kind: 源文件路径}，kind 取自 ASSET_KINDS；默认移动（move=True）而非复制，
                    因为源文件位于可随时清空的 temp/
            entry_id: 指定 id（通常复用 task_id）；缺省或冲突时自动生成
            move: False 时复制而非移动（导入历史数据用）

        Returns:
            入库后的列表项（含 size_bytes、assets）
        """
        with self._lock:
            eid = entry_id if (entry_id and _SAFE_ID.match(entry_id)) else new_id()
            if self._exists(eid):
                eid = new_id()
            target = self._entry_dir(eid)
            target.mkdir(parents=True, exist_ok=True)

            stored_assets: Dict[str, str] = {}
            for kind, src in (assets or {}).items():
                if kind not in ASSET_KINDS or not src:
                    continue
                src = Path(src)
                if not src.is_file():
                    logger.warning(f"入库跳过缺失产物 {kind}: {src}")
                    continue
                dest = target / f"{ASSET_BASENAMES[kind]}{src.suffix.lower()}"
                try:
                    if move:
                        shutil.move(str(src), str(dest))
                    else:
                        shutil.copy2(str(src), str(dest))
                    stored_assets[kind] = dest.name
                except OSError as e:
                    logger.warning(f"入库产物失败 {kind}: {e}")

            record = dict(entry)
            record["id"] = eid
            record.setdefault("created_at", time.time())
            record["assets"] = stored_assets
            self._write_entry_json(target, record)

            size_bytes = _dir_size(target)
            self._db.execute(
                """INSERT INTO entries (
                       id, created_at, title, platform, source_url, lang_src, lang_dst,
                       model, source_mode, duration_ms, elapsed_ms, size_bytes, favorite,
                       no_speech, has_audio, has_subtitles, has_summary, has_translation, assets
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    eid,
                    float(record.get("created_at") or time.time()),
                    str(record.get("title") or "")[:500],
                    record.get("platform"),
                    record.get("source_url"),
                    record.get("lang_src"),
                    record.get("lang_dst"),
                    record.get("model"),
                    record.get("source_mode"),
                    int(record.get("duration_ms") or 0),
                    int(record.get("elapsed_ms") or 0),
                    size_bytes,
                    1 if record.get("favorite") else 0,
                    1 if record.get("no_speech") else 0,
                    1 if "audio" in stored_assets else 0,
                    1 if ("srt" in stored_assets or "vtt" in stored_assets) else 0,
                    1 if (record.get("summary") or "").strip() else 0,
                    1 if (record.get("translation") or "").strip() else 0,
                    json.dumps(stored_assets, ensure_ascii=False),
                ),
            )
            self._db.commit()
            logger.info(f"入库完成: {eid} ({size_bytes} 字节, 产物 {list(stored_assets)})")
            return self.get_meta(eid) or {}

    def _write_entry_json(self, target: Path, record: Dict[str, Any]) -> None:
        """原子写 entry.json（先写临时文件再替换，避免崩溃留下半截 JSON）。"""
        tmp = target / "entry.json.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
        os.replace(str(tmp), str(target / "entry.json"))

    def import_entries(self, entries: Iterable[Dict[str, Any]]) -> int:
        """批量导入（localStorage 历史迁移）。产物文件已丢失，只搬文本。"""
        count = 0
        for e in entries:
            try:
                self.add(e, assets=None, entry_id=e.get("id"))
                count += 1
            except Exception as exc:
                logger.warning(f"导入历史条目失败: {exc}")
        return count

    # ── 读取 ──────────────────────────────────────────────

    def _exists(self, entry_id: str) -> bool:
        cur = self._db.execute("SELECT 1 FROM entries WHERE id = ?", (entry_id,))
        return cur.fetchone() is not None

    def list(
        self,
        q: str = "",
        limit: int = 50,
        offset: int = 0,
        favorites_only: bool = False,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """按创建时间倒序分页；q 匹配标题（不区分大小写）。返回 (items, total)。"""
        limit = max(1, min(int(limit or 50), 500))
        offset = max(0, int(offset or 0))
        where, params = [], []
        if q and q.strip():
            where.append("LOWER(title) LIKE ?")
            params.append(f"%{q.strip().lower()}%")
        if favorites_only:
            where.append("favorite = 1")
        clause = f" WHERE {' AND '.join(where)}" if where else ""

        with self._lock:
            total = self._db.execute(
                f"SELECT COUNT(*) FROM entries{clause}", params
            ).fetchone()[0]
            rows = self._db.execute(
                f"SELECT {', '.join(_LIST_COLUMNS)} FROM entries{clause}"
                " ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
        return [self._row_to_item(r) for r in rows], total

    def get_meta(self, entry_id: str) -> Optional[Dict[str, Any]]:
        """只读索引行，不碰磁盘上的 entry.json。"""
        with self._lock:
            row = self._db.execute(
                f"SELECT {', '.join(_LIST_COLUMNS)} FROM entries WHERE id = ?", (entry_id,)
            ).fetchone()
        return self._row_to_item(row) if row else None

    def get(self, entry_id: str) -> Optional[Dict[str, Any]]:
        """完整记录（entry.json + 索引里的 size_bytes/favorite）。"""
        meta = self.get_meta(entry_id)
        if not meta:
            return None
        path = self._entry_dir(entry_id) / "entry.json"
        try:
            with open(path, "r", encoding="utf-8") as f:
                record = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"读取 entry.json 失败 {entry_id}: {e}")
            return meta
        record.update({
            "size_bytes": meta["size_bytes"],
            "favorite": meta["favorite"],
        })
        return record

    def asset_path(self, entry_id: str, kind: str) -> Optional[Path]:
        """产物文件的绝对路径；不存在返回 None。"""
        if kind not in ASSET_KINDS or not _SAFE_ID.match(entry_id or ""):
            return None
        with self._lock:
            row = self._db.execute("SELECT assets FROM entries WHERE id = ?", (entry_id,)).fetchone()
        if not row:
            return None
        try:
            assets = json.loads(row["assets"] or "{}")
        except json.JSONDecodeError:
            return None
        name = assets.get(kind)
        if not name:
            return None
        entry_dir = self._entry_dir(entry_id).resolve()
        path = (entry_dir / name).resolve()
        # 双保险：文件名来自库自身，仍校验解析后仍在记录目录内
        if path.parent != entry_dir or not path.is_file():
            return None
        return path

    # ── 修改 / 删除 ───────────────────────────────────────

    def update(
        self,
        entry_id: str,
        title: Optional[str] = None,
        favorite: Optional[bool] = None,
    ) -> Optional[Dict[str, Any]]:
        """重命名 / 标星。两者同步写入索引与 entry.json。"""
        with self._lock:
            if not self._exists(entry_id):
                return None
            sets, params = [], []
            if title is not None:
                sets.append("title = ?")
                params.append(str(title)[:500])
            if favorite is not None:
                sets.append("favorite = ?")
                params.append(1 if favorite else 0)
            if not sets:
                return self.get_meta(entry_id)
            self._db.execute(
                f"UPDATE entries SET {', '.join(sets)} WHERE id = ?", (*params, entry_id)
            )
            self._db.commit()

            target = self._entry_dir(entry_id)
            try:
                with open(target / "entry.json", "r", encoding="utf-8") as f:
                    record = json.load(f)
                if title is not None:
                    record["title"] = str(title)[:500]
                if favorite is not None:
                    record["favorite"] = bool(favorite)
                self._write_entry_json(target, record)
            except (OSError, json.JSONDecodeError) as e:
                logger.warning(f"同步 entry.json 失败 {entry_id}: {e}")
            return self.get_meta(entry_id)

    def delete(self, entry_id: str) -> bool:
        """彻底删除：先删索引行，再删目录（顺序保证 UI 不会列出半删记录）。"""
        with self._lock:
            if not self._exists(entry_id):
                return False
            target = self._entry_dir(entry_id)
            self._db.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
            self._db.commit()
        try:
            shutil.rmtree(str(target), ignore_errors=True)
        except OSError as e:
            logger.warning(f"删除记录目录失败 {entry_id}: {e}")
        logger.info(f"记录已删除: {entry_id}")
        return True

    def drop_assets(self, entry_id: str, kinds: Iterable[str] = ("audio", "video")) -> int:
        """只删产物、保留文本（“释放空间”用）。返回释放的字节数。"""
        with self._lock:
            row = self._db.execute("SELECT assets FROM entries WHERE id = ?", (entry_id,)).fetchone()
            if not row:
                return 0
            try:
                assets = json.loads(row["assets"] or "{}")
            except json.JSONDecodeError:
                return 0

            freed = 0
            for kind in list(kinds):
                path = self.asset_path(entry_id, kind)
                if path:
                    try:
                        freed += path.stat().st_size
                        path.unlink()
                        assets.pop(kind, None)
                    except OSError as e:
                        logger.warning(f"删除产物失败 {entry_id}/{kind}: {e}")
            if not freed:
                return 0

            target = self._entry_dir(entry_id)
            self._db.execute(
                """UPDATE entries SET assets = ?, size_bytes = ?,
                       has_audio = ?, has_subtitles = ? WHERE id = ?""",
                (
                    json.dumps(assets, ensure_ascii=False),
                    _dir_size(target),
                    1 if "audio" in assets else 0,
                    1 if ("srt" in assets or "vtt" in assets) else 0,
                    entry_id,
                ),
            )
            self._db.commit()
            try:
                with open(target / "entry.json", "r", encoding="utf-8") as f:
                    record = json.load(f)
                record["assets"] = assets
                self._write_entry_json(target, record)
            except (OSError, json.JSONDecodeError):
                pass
            return freed

    # ── 统计 / 维护 ───────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        """记录数、总字节、最早/最晚时间、媒体占用（用于“磁盘占用”面板）。"""
        with self._lock:
            row = self._db.execute(
                """SELECT COUNT(*) AS count, COALESCE(SUM(size_bytes), 0) AS bytes,
                          MIN(created_at) AS oldest, MAX(created_at) AS newest,
                          COALESCE(SUM(favorite), 0) AS favorites,
                          COALESCE(SUM(CASE WHEN has_audio THEN size_bytes ELSE 0 END), 0) AS media_bytes
                   FROM entries"""
            ).fetchone()
        return {
            "count": row["count"],
            "bytes": row["bytes"],
            "media_bytes": row["media_bytes"],
            "favorites": row["favorites"],
            "oldest": row["oldest"],
            "newest": row["newest"],
            "root": str(self.root),
        }

    def purge_candidates(self, older_than_days: float = 30.0, keep_favorites: bool = True) -> List[Dict[str, Any]]:
        """“释放空间”对话框用：列出可丢弃媒体的记录，不做任何删除。"""
        cutoff = time.time() - max(0.0, float(older_than_days)) * 86400.0
        clause = "WHERE has_audio = 1 AND created_at < ?"
        params: List[Any] = [cutoff]
        if keep_favorites:
            clause += " AND favorite = 0"
        with self._lock:
            rows = self._db.execute(
                f"SELECT {', '.join(_LIST_COLUMNS)} FROM entries {clause} ORDER BY size_bytes DESC",
                params,
            ).fetchall()
        return [self._row_to_item(r) for r in rows]

    def gc(
        self,
        max_bytes: Optional[int] = None,
        max_age_days: Optional[float] = None,
        keep_favorites: bool = True,
    ) -> Dict[str, Any]:
        """配额清理。**默认不启用**：两个上限都为 None 时立即返回，不删任何内容。

        由调用方按 AVT_LIBRARY_QUOTA / AVT_LIBRARY_MAX_AGE_DAYS 决定是否传参。
        """
        if not max_bytes and not max_age_days:
            return {"removed": 0, "freed": 0, "skipped": True}

        removed, freed = 0, 0
        fav_clause = " AND favorite = 0" if keep_favorites else ""

        if max_age_days:
            cutoff = time.time() - float(max_age_days) * 86400.0
            with self._lock:
                rows = self._db.execute(
                    f"SELECT id, size_bytes FROM entries WHERE created_at < ?{fav_clause}",
                    (cutoff,),
                ).fetchall()
            for r in rows:
                if self.delete(r["id"]):
                    removed += 1
                    freed += r["size_bytes"]

        if max_bytes:
            while self.stats()["bytes"] > int(max_bytes):
                with self._lock:
                    row = self._db.execute(
                        f"SELECT id, size_bytes FROM entries WHERE 1=1{fav_clause}"
                        " ORDER BY created_at ASC LIMIT 1"
                    ).fetchone()
                if not row or not self.delete(row["id"]):
                    break
                removed += 1
                freed += row["size_bytes"]

        if removed:
            logger.info(f"库清理: 删除 {removed} 条, 释放 {freed} 字节")
        return {"removed": removed, "freed": freed, "skipped": False}

    def reindex(self) -> Dict[str, int]:
        """按磁盘重建索引：补录孤立目录、剔除目录已消失的行。启动时调用一次。"""
        added = dropped = 0
        with self._lock:
            known = {r["id"] for r in self._db.execute("SELECT id FROM entries").fetchall()}

        on_disk = set()
        for child in self.root.iterdir():
            if not child.is_dir() or not _SAFE_ID.match(child.name):
                continue
            on_disk.add(child.name)
            if child.name in known or not (child / "entry.json").is_file():
                continue
            try:
                with open(child / "entry.json", "r", encoding="utf-8") as f:
                    record = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            # 目录已在库内：只补索引行，不再搬动文件
            self._insert_existing(child.name, record, _dir_size(child))
            added += 1

        for missing in known - on_disk:
            with self._lock:
                self._db.execute("DELETE FROM entries WHERE id = ?", (missing,))
                self._db.commit()
            dropped += 1

        if added or dropped:
            logger.info(f"库索引重建: 补录 {added} 条, 剔除 {dropped} 条")
        return {"added": added, "dropped": dropped}

    def _insert_existing(self, entry_id: str, record: Dict[str, Any], size_bytes: int) -> None:
        assets = record.get("assets") or {}
        with self._lock:
            self._db.execute(
                """INSERT OR REPLACE INTO entries (
                       id, created_at, title, platform, source_url, lang_src, lang_dst,
                       model, source_mode, duration_ms, elapsed_ms, size_bytes, favorite,
                       no_speech, has_audio, has_subtitles, has_summary, has_translation, assets
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    entry_id,
                    float(record.get("created_at") or time.time()),
                    str(record.get("title") or "")[:500],
                    record.get("platform"),
                    record.get("source_url"),
                    record.get("lang_src"),
                    record.get("lang_dst"),
                    record.get("model"),
                    record.get("source_mode"),
                    int(record.get("duration_ms") or 0),
                    int(record.get("elapsed_ms") or 0),
                    size_bytes,
                    1 if record.get("favorite") else 0,
                    1 if record.get("no_speech") else 0,
                    1 if "audio" in assets else 0,
                    1 if ("srt" in assets or "vtt" in assets) else 0,
                    1 if (record.get("summary") or "").strip() else 0,
                    1 if (record.get("translation") or "").strip() else 0,
                    json.dumps(assets, ensure_ascii=False),
                ),
            )
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()
