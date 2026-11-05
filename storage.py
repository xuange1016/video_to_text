"""Durable batch state and transcript records in one local SQLite database."""
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from engine import export_contents, export_result, process_media
from platforms import Cancelled, Media, MediaError, MESSAGES


ACTIVE = ("queued", "subtitle", "audio", "model", "loading", "transcribing", "saving")
RETRYABLE = ("failed", "cancelled", "interrupted", "save_pending")
LABELS = dict(zip(
    (*ACTIVE, *RETRYABLE, "done"),
    ("等待处理", "获取平台字幕", "获取音频", "下载模型", "加载模型", "语音转写", "保存文件",
     "失败", "已取消", "已中断", "待保存", "已完成"),
))


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
                    force_audio INTEGER NOT NULL DEFAULT 0, parent_job INTEGER REFERENCES jobs(id)
                );
                CREATE INDEX IF NOT EXISTS jobs_batch_idx ON jobs(batch, id);
            """)
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

    def create(self, items, options, parent=None):
        with self.connect() as db:
            batch = db.execute(
                "INSERT INTO batches (created, options) VALUES (?, ?)",
                (datetime.now().astimezone().isoformat(timespec="seconds"), json.dumps(options)),
            ).lastrowid
            db.executemany(
                "INSERT INTO jobs (batch, media, force_audio, parent_job) VALUES (?, ?, ?, ?)",
                [(batch, json.dumps(asdict(media), ensure_ascii=False), int(parent is not None), parent)
                 for media in items],
            )
            return batch

    def change(self, job, **values):
        # Names originate from the worker and GUI controller.
        columns = ", ".join(f"{name}=?" for name in values)
        with self.connect() as db:
            db.execute(f"UPDATE jobs SET {columns} WHERE id=?", (*values.values(), job))

    def item(self, job):
        with self.connect() as db:
            row = db.execute("""
                SELECT jobs.*, batches.options FROM jobs JOIN batches ON batches.id=jobs.batch WHERE jobs.id=?
            """, (job,)).fetchone()
        return dict(row) if row else None

    def list_jobs(self, batch):
        with self.connect() as db:
            rows = db.execute("""
                SELECT id, media, status, progress, error, destination,
                result IS NOT NULL AS hasResult FROM jobs WHERE batch=? ORDER BY id
            """, (batch,)).fetchall()
        return [json.loads(row["media"]) | dict(row) | {"stateLabel": LABELS[row["status"]]}
                for row in rows]

    def list_batches(self):
        with self.connect() as db:
            rows = db.execute("""
                SELECT batches.id, batches.created, COUNT(jobs.id) AS total,
                SUM(jobs.status='done') AS done, SUM(jobs.status IN ('failed','cancelled','interrupted','save_pending')) AS failed
                FROM batches JOIN jobs ON jobs.batch=batches.id GROUP BY batches.id ORDER BY batches.id DESC
            """).fetchall()
        return [dict(row) for row in rows]

    def jobs_to_run(self, batch, item=None):
        with self.connect() as db:
            rows = db.execute("""
                SELECT id, status FROM jobs WHERE batch=? AND (? IS NULL OR id=?) ORDER BY id
            """, (batch, item, item)).fetchall()
        return [row["id"] for row in rows if row["status"] in (*RETRYABLE, "queued")]


def run_batch(store, batch, item, output_override, platforms, transcriber, ffmpeg, temp, notify, cancel):
    for job_id in store.jobs_to_run(batch, item):
        job = store.item(job_id)
        if cancel():
            saved_result = bool(job["result"])
            store.change(job_id, status="save_pending" if saved_result else "cancelled",
                         progress=None, error="已取消，可补存文稿。" if saved_result else "已取消，可单独重试。")
            notify()
            continue
        media = Media(**json.loads(job["media"]))
        options = json.loads(job["options"])
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
                store.change(job_id, status=status, progress=progress, error="", code="")
                notify()

        try:
            if not result:
                result = process_media(platforms, transcriber, ffmpeg, media, options, temp, report, cancel)
                store.change(job_id, result=json.dumps(result, ensure_ascii=False))
            if cancel():
                raise Cancelled
            report("saving", None)
            files = export_contents(result, options["format"])
            suffix = "重转" if job["parent_job"] else (
                media.key.split(":", 1)[1].replace(":", "-") if media.source != "本地文件"
                else "本地 " + hashlib.blake2s(media.key.encode("utf-8"), digest_size=4).hexdigest())
            destination = export_result(
                job["target"] or options["root"], media.title, suffix, files, job["destination"],
                lambda name: store.change(job_id, destination=name),
            )
            store.change(job_id, status="done", destination=destination, progress=1, error="", code="")
        except Cancelled:
            store.change(job_id, status="save_pending" if result else "cancelled", progress=None,
                         error="已取消，可补存文稿。" if result else "已取消，可单独重试。")
        except MediaError as exc:
            store.change(job_id, status="failed", progress=None, error=str(exc), code=exc.code)
        except OSError:
            code = "save" if result else "local"
            store.change(job_id, status="save_pending" if result else "failed", progress=None,
                         error=MESSAGES[code], code=code)
        except Exception:
            code = "save" if result else "unexpected"
            store.change(job_id, status="save_pending" if result else "failed", progress=None,
                         error=MESSAGES[code], code=code)
        notify()
