"""Durable batch state and transcript records in one local SQLite database."""
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from downloads import download_video, publish_video
from engine import export_contents, export_result, process_media
from platforms import Cancelled, Media, MediaError, MESSAGES


ACTIVE = ("queued", "subtitle", "audio", "model", "loading", "transcribing", "media", "downloading", "muxing", "saving")
RETRYABLE = ("failed", "cancelled", "interrupted", "save_pending")
LABELS = {
    "queued": "等待处理", "subtitle": "获取平台字幕", "audio": "获取音频",
    "model": "下载模型", "loading": "加载模型", "transcribing": "语音转写",
    "media": "获取视频信息", "downloading": "下载视频", "muxing": "合并音视频", "saving": "保存文件",
    "failed": "失败", "cancelled": "已取消", "interrupted": "已中断",
    "save_pending": "待保存", "done": "已完成", "waiting": "待启动",
}
OPTION_FIELDS = {"transcribe": ("root", "model", "format"), "download": ("root", "quality")}
KIND_LABELS = {"transcribe": "提取文案", "download": "保存视频"}


def compact_title(title):
    title = " ".join(title.split())
    return title if len(title) <= 24 else title[:24].rstrip() + "…"


def effective_options(row):
    return json.loads(row["batch_options"]) | json.loads(row["job_options"] or "{}")


class Store:
    def __init__(self, path):
        self.path = path
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS batches (
                    id INTEGER PRIMARY KEY, created TEXT NOT NULL, options TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY, batch INTEGER NOT NULL REFERENCES batches(id),
                    media TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
                    result TEXT, error TEXT NOT NULL DEFAULT '', code TEXT NOT NULL DEFAULT '',
                    progress REAL, target TEXT NOT NULL DEFAULT '', destination TEXT NOT NULL DEFAULT '',
                    force_audio INTEGER NOT NULL DEFAULT 0, parent_job INTEGER REFERENCES jobs(id),
                    alias TEXT NOT NULL DEFAULT '', job_options TEXT
                );
                CREATE INDEX IF NOT EXISTS jobs_batch_idx ON jobs(batch, id);
                CREATE INDEX IF NOT EXISTS jobs_status_idx ON jobs(status, id);
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(jobs)")}
            if "alias" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN alias TEXT NOT NULL DEFAULT ''")
            if "job_options" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN job_options TEXT")
            parameters = ",".join("?" * len(ACTIVE))
            db.execute(f"""
                UPDATE jobs SET status=CASE WHEN result IS NULL THEN 'interrupted' ELSE 'save_pending' END,
                error='上次处理已中断，可继续该任务。', progress=NULL
                WHERE status IN ({parameters})
            """, ACTIVE)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=12)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def settings(self, defaults):
        with self.connect() as db:
            row = db.execute("SELECT data FROM settings WHERE id=1").fetchone()
            return {**defaults, **(json.loads(row["data"]) if row else {})}

    def save_settings(self, settings):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO settings (id, data) VALUES (1, ?)", (json.dumps(settings),))

    def create(self, items, options, parent=None, waiting=False):
        with self.connect() as db:
            batch = db.execute(
                "INSERT INTO batches (created, options) VALUES (?, ?)",
                (datetime.now().astimezone().isoformat(timespec="seconds"), json.dumps(options)),
            ).lastrowid
            db.executemany(
                "INSERT INTO jobs (batch, media, force_audio, parent_job, status) VALUES (?, ?, ?, ?, ?)",
                [(batch, json.dumps(asdict(media), ensure_ascii=False), int(parent is not None), parent,
                  "waiting" if waiting else "queued") for media in items],
            )
            return batch

    def edit_waiting(self, job, alias, options):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""
                SELECT jobs.job_options, batches.options AS batch_options
                FROM jobs JOIN batches ON batches.id=jobs.batch WHERE jobs.id=? AND jobs.status='waiting'
            """, (job,)).fetchone()
            if row is None:
                return False
            kind = effective_options(row).get("kind", "transcribe")
            overrides = json.loads(row["job_options"] or "{}") | {
                key: options[key] for key in OPTION_FIELDS[kind] if key in options
            }
            cursor = db.execute("""
                UPDATE jobs SET alias=?, job_options=? WHERE id=? AND status='waiting'
            """, (" ".join(alias.split()), json.dumps(overrides, ensure_ascii=False), job))
            return cursor.rowcount == 1

    def edit_waiting_many(self, jobs, options):
        jobs = sorted(set(jobs))
        if not jobs:
            return 0
        placeholders = ",".join("?" for _ in jobs)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(f"""
                SELECT jobs.id, jobs.job_options, batches.options AS batch_options
                FROM jobs JOIN batches ON batches.id=jobs.batch
                WHERE jobs.id IN ({placeholders}) AND jobs.status='waiting'
            """, jobs).fetchall()
            changes = []
            for row in rows:
                kind = effective_options(row).get("kind", "transcribe")
                patch = {key: options[key] for key in OPTION_FIELDS[kind] if key in options}
                if patch:
                    overrides = json.loads(row["job_options"] or "{}") | patch
                    changes.append((json.dumps(overrides, ensure_ascii=False), row["id"]))
            db.executemany("UPDATE jobs SET job_options=? WHERE id=? AND status='waiting'", changes)
            return len(changes)

    def delete_waiting_many(self, jobs):
        jobs = sorted(set(jobs))
        if not jobs:
            return 0
        placeholders = ",".join("?" for _ in jobs)
        with self.connect() as db:
            rows = db.execute(f"""
                DELETE FROM jobs WHERE id IN ({placeholders}) AND status='waiting'
                AND NOT EXISTS (SELECT 1 FROM jobs child WHERE child.parent_job=jobs.id)
                RETURNING batch
            """, jobs).fetchall()
            for batch in {row["batch"] for row in rows}:
                db.execute("DELETE FROM batches WHERE id=? AND NOT EXISTS (SELECT 1 FROM jobs WHERE batch=?)",
                           (batch, batch))
            return len(rows)

    def delete_waiting(self, job):
        with self.connect() as db:
            row = db.execute("""
                DELETE FROM jobs WHERE id=? AND status='waiting'
                AND NOT EXISTS (SELECT 1 FROM jobs child WHERE child.parent_job=?)
                RETURNING batch
            """, (job, job)).fetchone()
            if row is None:
                return False
            db.execute("""
                DELETE FROM batches WHERE id=? AND NOT EXISTS (SELECT 1 FROM jobs WHERE batch=?)
            """, (row["batch"], row["batch"]))
            return True

    def release_waiting(self, job=0):
        with self.connect() as db:
            rows = db.execute("""
                UPDATE jobs SET status='queued' WHERE status='waiting' AND (?=0 OR id=?) RETURNING id
            """, (job, job)).fetchall()
            return sorted(row["id"] for row in rows)

    def prepare_retry(self, batch, job=0):
        statuses = ",".join("?" * len(RETRYABLE))
        with self.connect() as db:
            rows = db.execute(f"""
                UPDATE jobs SET status='queued', progress=NULL, error='', code=''
                WHERE batch=? AND (?=0 OR id=?) AND status IN ({statuses}) RETURNING id
            """, (batch, job, job, *RETRYABLE)).fetchall()
            return sorted(row["id"] for row in rows)

    def change(self, job, **values):
        # Names originate from the worker and GUI controller.
        columns = ", ".join(f"{name}=?" for name in values)
        with self.connect() as db:
            db.execute(f"UPDATE jobs SET {columns} WHERE id=?", (*values.values(), job))

    def item(self, job):
        with self.connect() as db:
            row = db.execute("""
                SELECT jobs.*, batches.options AS batch_options
                FROM jobs JOIN batches ON batches.id=jobs.batch WHERE jobs.id=?
            """, (job,)).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["options"] = json.dumps(effective_options(row), ensure_ascii=False)
        del item["batch_options"]
        return item

    def list_jobs(self, batch):
        return self._job_rows("jobs.batch=?", (batch,))

    def waiting_jobs(self):
        return self._job_rows("jobs.status=?", ("waiting",))

    def active_jobs(self):
        parameters = ",".join("?" * len(ACTIVE))
        return self._job_rows(f"jobs.status IN ({parameters})", ACTIVE)

    def _job_rows(self, predicate, parameters):
        with self.connect() as db:
            rows = db.execute(f"""
                SELECT jobs.id, jobs.batch, jobs.media, jobs.status, jobs.progress, jobs.error,
                       jobs.destination, jobs.alias, jobs.job_options, batches.options AS batch_options,
                       jobs.result IS NOT NULL AS hasResult
                FROM jobs JOIN batches ON batches.id=jobs.batch WHERE {predicate} ORDER BY jobs.id
            """, parameters).fetchall()
        jobs = []
        for row in rows:
            media = json.loads(row["media"])
            options = effective_options(row)
            kind = options.get("kind", "transcribe")
            details = dict(row)
            del details["job_options"], details["batch_options"]
            jobs.append(media | details | {
                "stateLabel": LABELS[row["status"]],
                "displayTitle": compact_title(row["alias"] or media["title"]),
                "locator": media["ref"],
                "kind": kind, "kindLabel": KIND_LABELS[kind], "quality": options.get("quality", "720"),
                **{key: options.get(key, "") for key in ("root", "model", "format")},
            })
        return jobs

    def list_batches(self):
        with self.connect() as db:
            rows = db.execute("""
                SELECT batches.id, batches.created, COUNT(jobs.id) AS total,
                SUM(jobs.status='done') AS done, SUM(jobs.status='waiting') AS waiting,
                SUM(jobs.status IN ('failed','cancelled','interrupted','save_pending')) AS failed
                FROM batches JOIN jobs ON jobs.batch=batches.id GROUP BY batches.id ORDER BY batches.id DESC
            """).fetchall()
        return [dict(row) for row in rows]

    def jobs_to_run(self, batch, item=None):
        with self.connect() as db:
            rows = db.execute("""
                SELECT id, status FROM jobs WHERE batch=? AND (? IS NULL OR id=?) ORDER BY id
            """, (batch, item, item)).fetchall()
        return [row["id"] for row in rows if row["status"] in (*RETRYABLE, "queued")]


def run_batch(store, batch, item, output_override, platforms, transcriber, ffmpeg, temp, notify, cancel, *, observe=None,
              media_cache=None):
    for job_id in store.jobs_to_run(batch, item):
        job = store.item(job_id)
        if cancel():
            saved_result = bool(job["result"])
            store.change(job_id, status="save_pending" if saved_result else "cancelled",
                         progress=None, error="已取消，可补存结果。" if saved_result else "已取消，可单独重试。")
            notify()
            continue
        media = Media(**json.loads(job["media"]))
        options = json.loads(job["options"])
        downloading = options.get("kind", "transcribe") == "download"
        options["force_audio"] = bool(job["force_audio"])
        if output_override:
            store.change(job_id, target=output_override)
            job["target"] = output_override
        result = json.loads(job["result"]) if job["result"] else None
        previous = [None, None]

        def report(status, progress):
            bucket = int(progress * 50) if progress is not None else None
            if [status, bucket] != previous:
                previous[:] = status, bucket
                if observe is not None:
                    observe(job_id, status, progress)
                store.change(job_id, status=status, progress=progress, error="", code="")
                notify()

        try:
            if not result:
                if downloading:
                    cache = Path(media_cache) if media_cache else Path(store.path).parent / "media-cache"
                    cache.mkdir(parents=True, exist_ok=True)
                    result = download_video(platforms, ffmpeg, media, options | {"_job_id": job_id}, cache, report, cancel)
                else:
                    result = process_media(platforms, transcriber, ffmpeg, media, options, temp, report, cancel)
                store.change(job_id, result=json.dumps(result, ensure_ascii=False))
            if cancel():
                raise Cancelled
            report("saving", None)
            suffix = "重转" if job["parent_job"] else (
                media.key.split(":", 1)[1].replace(":", "-") if media.source != "本地文件"
                else "本地 " + hashlib.blake2s(media.key.encode("utf-8"), digest_size=4).hexdigest())
            if downloading:
                destination = publish_video(
                    job["target"] or options["root"], media.title, suffix + " 视频", result, job["destination"],
                    lambda name: store.change(job_id, destination=name), cancel,
                )
            else:
                destination = export_result(
                    job["target"] or options["root"], media.title, suffix, export_contents(result, options["format"]),
                    job["destination"], lambda name: store.change(job_id, destination=name),
                )
            store.change(job_id, status="done", destination=destination, progress=1, error="", code="")
        except Cancelled:
            store.change(job_id, status="save_pending" if result else "cancelled", progress=None,
                         error="已取消，可补存结果。" if result else "已取消，可单独重试。")
        except MediaError as exc:
            if exc.code == "cache_missing":
                result = None
                store.change(job_id, result=None)
            store.change(job_id, status="save_pending" if result else "failed", progress=None,
                         error=str(exc), code=exc.code)
        except OSError:
            code = ("video_save" if downloading else "save") if result else "local"
            store.change(job_id, status="save_pending" if result else "failed", progress=None,
                         error=MESSAGES[code], code=code)
        except Exception:
            code = ("video_save" if downloading else "save") if result else "unexpected"
            store.change(job_id, status="save_pending" if result else "failed", progress=None,
                         error=MESSAGES[code], code=code)
        notify()
