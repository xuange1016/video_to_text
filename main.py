"""Desktop entry point and the QML-to-Python controller."""
from __future__ import annotations

import json
import logging
import math
import os
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import Property, QLockFile, QObject, QStandardPaths, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QFontDatabase, QGuiApplication, QIcon
from PySide6.QtQml import QQmlApplicationEngine

from engine import MAX_PARALLEL, MODEL_SIZES, model_path, result_text, timestamp, Transcriber
from platforms import Cancelled, Media, MESSAGES, Platforms
from storage import Store, compact_title, run_batch


APP = Path(__file__).resolve().parent
ASSETS = Path(sys.argv[0]).resolve().parent if getattr(sys, "frozen", False) or "__compiled__" in globals() else APP
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")


def binary(name):
    name += ".exe" if sys.platform == "win32" else ""
    for root in (ASSETS, APP):
        for path in (root / "tools" / name, root / ".build" / "tools" / name):
            if path.is_file():
                return str(path)
    return shutil.which(name)


class Controller(QObject):
    stateChanged = Signal()
    jobsChanged = Signal()
    queueChanged = Signal()
    activeChanged = Signal()
    historyChanged = Signal()
    batchChanged = Signal()
    feedback = Signal(str)
    _finished = Signal(str, object, str)
    _jobFinished = Signal(int, str)

    def __init__(self, data):
        super().__init__()
        self.data = data
        self.temp = data / "scratch"
        if self.temp.is_dir():
            shutil.rmtree(self.temp, ignore_errors=True)
        self.temp.mkdir(exist_ok=True)
        self.models = data / "models"
        self.models.mkdir(exist_ok=True)
        self.store = Store(data / "tasks.sqlite3")
        documents = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
        self._settings = self.store.settings({"root": str(Path(documents) / "片语导出"),
                                              "model": "medium", "format": "both", "parallelism": 1})
        self._inputs = []
        self._checked = set()
        self._duplicates = 0
        self._folders = []
        self._stage = 1
        self._batch = 0
        self._job = 0
        self._task = ""
        self._detail = {}
        self._jobs = []
        self._history = self.store.list_batches()
        self._waiting = self.store.waiting_jobs()
        self._active_jobs = []
        self._parse_stop = threading.Event()
        self._pending = {}
        self._inflight = {}
        self._estimates = {}
        self._estimate_lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=MAX_PARALLEL, thread_name_prefix="pianyu")
        self._timer = QTimer(self, interval=250)
        self._timer.timeout.connect(self.refresh)
        self._exit = False
        self._font_family = "Microsoft YaHei UI"
        for font in (ASSETS / "ui" / "NotoSansSC.otf", APP / ".build" / "fonts" / "NotoSansSC.otf"):
            if font.is_file():
                names = QFontDatabase.applicationFontFamilies(QFontDatabase.addApplicationFont(str(font)))
                if names:
                    self._font_family = names[0]
                    break
        self.ffmpeg = binary("ffmpeg")
        self.platforms = Platforms(binary("ffprobe"), binary("yt-dlp"))
        self.transcriber = Transcriber(self.models)
        self._finished.connect(self.finish)
        self._jobFinished.connect(self.job_finished)

    @Property("QVariantMap", notify=stateChanged)
    def settings(self):
        return self._settings

    @Property(str, constant=True)
    def fontFamily(self):
        return self._font_family

    @Property("QVariantMap", notify=stateChanged)
    def modelsInfo(self):
        return {name: {"ready": bool(model_path(name, self.models)), "size": size}
                for name, size in MODEL_SIZES.items()}

    @Property("QVariantList", notify=stateChanged)
    def folders(self):
        return self._folders

    @Property("QVariantList", notify=stateChanged)
    def inputs(self):
        return [media.__dict__ | {"checked": media.key in self._checked,
                                  "displayTitle": compact_title(media.title)} for media in self._inputs]

    @Property("QVariantMap", notify=stateChanged)
    def counts(self):
        return {**{source: sum(1 for m in self._inputs if m.source == source and not m.error)
                   for source in ("抖音", "B 站", "本地文件")},
                "duplicates": self._duplicates, "invalid": sum(bool(m.error) for m in self._inputs)}

    @Property(int, notify=stateChanged)
    def selectedCount(self):
        return len(self._checked)

    @Property(int, notify=stateChanged)
    def stage(self):
        return self._stage

    @Property(str, notify=stateChanged)
    def task(self):
        return self._task or ("batch" if self.running else "")

    @Property(bool, notify=stateChanged)
    def busy(self):
        return bool(self._task) or self.running

    @Property(bool, notify=stateChanged)
    def running(self):
        return bool(self._pending or self._inflight)

    @Property(bool, notify=stateChanged)
    def canCancel(self):
        return any(batch == self._batch for batch, _ in self._pending.values()) or any(
            batch == self._batch and not stop.is_set() for batch, stop in self._inflight.values())

    @Property("QVariantList", notify=jobsChanged)
    def jobs(self):
        return self._jobs

    @Property("QVariantList", notify=queueChanged)
    def waitingJobs(self):
        return self._waiting

    @Property("QVariantList", notify=activeChanged)
    def activeJobs(self):
        return self._active_jobs

    @Property("QVariantList", notify=historyChanged)
    def history(self):
        return self._history

    @Property("QVariantMap", notify=jobsChanged)
    def detail(self):
        return self._detail

    @Property(int, notify=batchChanged)
    def batchId(self):
        return self._batch

    @Slot(QUrl)
    def addFolder(self, url):
        folder = url.toLocalFile()
        if folder and folder not in self._folders:
            self._folders = [*self._folders, folder]
            self.stateChanged.emit()

    @Slot(str)
    def removeFolder(self, folder):
        self._folders = [item for item in self._folders if item != folder]
        self.stateChanged.emit()

    @Slot(str)
    def resolve(self, text):
        if not text.strip() and not self._folders:
            self.feedback.emit("请粘贴链接或文件路径，或者选择文件夹。")
            return
        folders = self._folders[:]
        self.start("resolve", lambda: self.platforms.resolve(text, folders, self._parse_stop.is_set))

    @Slot(str, bool)
    def selectItem(self, key, checked):
        if key:
            (self._checked.add if checked else self._checked.discard)(key)
            self.stateChanged.emit()

    @Slot()
    def selectAll(self):
        available = {m.key for m in self._inputs if not m.error}
        self._checked = set() if self._checked == available else available
        self.stateChanged.emit()

    @Slot()
    def back(self):
        if not self._task:
            self._stage = 1
            self.stateChanged.emit()

    @Slot()
    def newBatch(self):
        if not self._task:
            self._stage, self._batch, self._job = 1, 0, 0
            self._inputs, self._folders, self._checked, self._duplicates = [], [], set(), 0
            self.refresh()
            self.batchChanged.emit()
            self.stateChanged.emit()

    @staticmethod
    def options(root, model, formats):
        if model not in MODEL_SIZES or formats not in {"txt", "srt", "both"} or not root.strip():
            raise ValueError("请填写保存目录，并选择模型和格式。")
        if not Path(root.strip()).is_absolute():
            raise ValueError("请填写保存目录的绝对路径。")
        return {"root": root.strip(), "model": model, "format": formats}

    @Slot(str, str, str)
    @Slot(str, str, str, int)
    def saveSettings(self, root, model, formats, parallelism=None):
        try:
            options = self.options(root, model, formats)
            parallelism = self._settings["parallelism"] if parallelism is None else parallelism
            if not 1 <= parallelism <= MAX_PARALLEL:
                raise ValueError(f"同时处理数应为 1 至 {MAX_PARALLEL}。")
            options["parallelism"] = parallelism
            self.store.save_settings(options)
        except (ValueError, OSError) as exc:
            self.feedback.emit(str(exc))
            return
        self._settings = options
        self.pump()
        self.stateChanged.emit()
        self.feedback.emit("设置已保存。")

    @Slot(str, str, str)
    def submit(self, root, model, formats):
        batch = self.create_selection(root, model, formats, waiting=False)
        if batch:
            self.openBatch(batch)
            self.schedule(self.store.jobs_to_run(batch))

    @Slot(str, str, str, result=bool)
    def enqueue(self, root, model, formats):
        batch = self.create_selection(root, model, formats, waiting=True)
        if not batch:
            return False
        self.newBatch()
        self.feedback.emit("素材已加入等待队列。")
        return True

    def create_selection(self, root, model, formats, *, waiting):
        items = [m for m in self._inputs if m.key in self._checked and not m.error]
        if not items or self._task or self._exit:
            return None
        try:
            options = self.options(root, model, formats)
            if waiting:
                current = {row["key"] for row in self.store.waiting_jobs() + self.store.active_jobs()}
                items = [media for media in items if media.key not in current]
                if not items:
                    self.feedback.emit("所选素材已在任务队列中。")
                    return None
            return self.store.create(items, options, waiting=waiting)
        except ValueError as exc:
            self.feedback.emit(str(exc))
            return None

    @Slot(int, str, str, str, str, result=bool)
    def editWaiting(self, job, alias, root, model, formats):
        try:
            options = self.options(root, model, formats)
            updated = self.store.edit_waiting(job, alias, options)
        except ValueError as exc:
            self.feedback.emit(str(exc))
            return False
        self.refresh()
        self.feedback.emit("任务已更新。" if updated else "任务已离开等待队列。")
        return updated

    @Slot(int)
    def removeWaiting(self, job):
        removed = self.store.delete_waiting(job)
        self.refresh()
        self.feedback.emit("已移除等待任务。" if removed else "任务已离开等待队列。")

    @Slot(int)
    def startWaiting(self, job):
        if not self._exit:
            self.schedule(self.store.release_waiting(job))

    @Slot(int)
    def openBatch(self, batch):
        self._stage = 3
        self.viewBatch(batch)

    @Slot(int)
    def viewBatch(self, batch):
        self._batch, self._job = batch, 0
        self.refresh()
        self.batchChanged.emit()
        self.stateChanged.emit()

    @Slot(int)
    def openJob(self, job):
        self._job = job
        self.refresh()

    @Slot(int, str)
    def retry(self, job, destination):
        batch = self._batch
        if self._exit:
            return
        if destination and not Path(destination).is_absolute():
            self.feedback.emit("请选择保存目录的绝对路径。")
            return
        jobs = self.store.prepare_retry(batch, job)
        if not jobs:
            self.feedback.emit("当前没有需要重试的任务。")
            return
        self.schedule(jobs, destination)

    @Slot(int)
    def forceAudio(self, job):
        original = self.store.item(job)
        if not original or self._exit:
            return
        media = Media(**json.loads(original["media"]))
        options = json.loads(original["options"])
        if original["target"]:
            options["root"] = original["target"]

        batch = self.store.create([media], options, parent=job)
        self.viewBatch(batch)
        self.schedule(self.store.jobs_to_run(batch))

    @Slot()
    def cancel(self):
        for job, (batch, _) in [*self._pending.items(), *self._inflight.items()]:
            if batch == self._batch:
                self.stop_job(job)
        self.refresh()
        self.stateChanged.emit()
        self.feedback.emit("正在停止，已完成的结果会保留。")

    @Slot(int)
    def cancelJob(self, job):
        self.stop_job(job)
        self.refresh()
        self.stateChanged.emit()

    @Slot()
    def cancelAndQuit(self):
        self._exit = True
        self._parse_stop.set()
        for job in [*self._pending, *self._inflight]:
            self.stop_job(job)
        self.maybe_quit()

    @Slot(str)
    def copy(self, value):
        QGuiApplication.clipboard().setText(value)
        self.feedback.emit("文案已复制。")

    @Slot(str)
    def openFolder(self, path):
        if not Path(path).is_dir() or not QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
            self.feedback.emit("无法打开该目录。")

    def start(self, kind, work):
        if self._task or self._exit:
            return
        self._task = kind
        self._parse_stop.clear()
        self.stateChanged.emit()

        def target():
            try:
                self._finished.emit(kind, work(), "")
            except Cancelled:
                self._finished.emit(kind, None, "")
            except ValueError as exc:
                self._finished.emit(kind, None, str(exc))
            except Exception:
                logging.exception("Worker operation failed")
                self._finished.emit(kind, None, "操作失败。请检查输入和存储权限后重试。")
        threading.Thread(target=target, daemon=True).start()

    def schedule(self, jobs, destination=""):
        for job in jobs:
            record = self.store.item(job)
            if destination:
                self.store.change(job, target=destination)
            self._pending[job] = (record["batch"], threading.Event())
        self.pump()
        self.refresh()
        self.stateChanged.emit()

    def pump(self):
        if self._exit:
            return
        while self._pending and len(self._inflight) < self._settings["parallelism"]:
            job = next((key for key in self._pending if key not in self._inflight), None)
            if job is None:
                break
            batch, stop = self._pending.pop(job)
            self._inflight[job] = (batch, stop)
            self._pool.submit(self.run_job, job, batch, stop)
        if self.running and not self._timer.isActive():
            self._timer.start()

    def run_job(self, job, batch, stop):
        error = ""
        try:
            platform = Platforms(self.platforms.ffprobe, self.platforms.ytdlp)
            with platform.session:
                run_batch(self.store, batch, job, "", platform, self.transcriber, self.ffmpeg, self.temp,
                          lambda: None, stop.is_set, observe=self.observe_progress)
        except Exception:
            logging.exception("Queue operation failed for job %s", job)
            error = MESSAGES["unexpected"]
            try:
                record = self.store.item(job)
                self.store.change(job, status="save_pending" if record["result"] else "failed",
                                  progress=None, error=error, code="unexpected")
            except Exception:
                logging.exception("Could not update job %s after failure", job)
        finally:
            self._jobFinished.emit(job, error)

    def stop_job(self, job):
        if job in self._pending:
            self._pending.pop(job)
            record = self.store.item(job)
            if record["result"]:
                self.store.change(job, status="save_pending", progress=None, error="已取消，可补存文稿。")
            else:
                self.store.change(job, status="cancelled", progress=None, error="已取消，可单独重试。")
        elif job in self._inflight:
            self._inflight[job][1].set()

    @Slot(int, str)
    def job_finished(self, job, error):
        self._inflight.pop(job, None)
        with self._estimate_lock:
            self._estimates.pop(job, None)
        self.pump()
        if not self.running:
            self._timer.stop()
        self.refresh()
        self.stateChanged.emit()
        if error:
            self.feedback.emit(error)
        self.maybe_quit()

    def observe_progress(self, job, stage, progress):
        with self._estimate_lock:
            if stage not in {"transcribing", "audio"} or progress is None:
                self._estimates.pop(job, None)
                return
            now = time.monotonic()
            old = self._estimates.get(job)
            if not old or stage != old[0] or progress < old[3]:
                self._estimates[job] = (stage, now, progress, progress, now, None)
                return
            _, started, initial, _, _, _ = old
            gain = progress - initial
            elapsed = now - started
            eta = math.ceil((1 - progress) * elapsed / gain) if elapsed >= 1.5 and gain >= .04 else None
            self._estimates[job] = (stage, started, initial, progress, now, eta)

    def add_estimate(self, row, estimates, now):
        estimate = estimates.get(row["id"])
        eta = None
        if estimate and row["status"] == estimate[0] and row["progress"] == estimate[3] and now - estimate[4] < 10:
            eta = estimate[5]
        stop = self._inflight.get(row["id"])
        return row | {"eta": eta, "stopping": bool(stop and stop[1].is_set())}

    @Slot(str, object, str)
    def finish(self, kind, result, error):
        self._task = ""
        if kind == "resolve" and result is not None:
            self._inputs, self._duplicates = result
            self._checked.clear()
            self._stage = 2
        self.refresh()
        self.stateChanged.emit()
        if error:
            self.feedback.emit(error)
        self.maybe_quit()

    def maybe_quit(self):
        if self._exit and not self.busy:
            self._pool.shutdown(wait=False)
            QGuiApplication.quit()

    @Slot()
    def refresh(self):
        with self._estimate_lock:
            estimates = self._estimates.copy()
        now = time.monotonic()
        jobs = [self.add_estimate(row, estimates, now) for row in self.store.list_jobs(self._batch)]
        active = [self.add_estimate(row, estimates, now) for row in self.store.active_jobs()]
        waiting = self.store.waiting_jobs()
        history = self.store.list_batches()
        if active != self._active_jobs:
            self._active_jobs = active
            self.activeChanged.emit()
        if waiting != self._waiting:
            self._waiting = waiting
            self.queueChanged.emit()
        if history != self._history:
            self._history = history
            self.historyChanged.emit()
        selected = next((m for m in jobs if m["id"] == self._job), None)
        if not selected and jobs:
            selected = next((m for m in jobs if m["hasResult"]), jobs[0])
            self._job = selected["id"]
        detail = dict(selected or {})
        if selected and selected["hasResult"]:
            if self._detail.get("id") == self._job and "text" in self._detail:
                detail.update({key: self._detail[key] for key in ("transcriptSource", "text", "timeline")})
            else:
                record = json.loads(self.store.item(self._job)["result"])
                detail.update(transcriptSource=record["source"], text=result_text(record),
                              timeline=[item | {"stamp": timestamp(item["start"]).split(",")[0]}
                                        for item in record["segments"]])
        if jobs != self._jobs or detail != self._detail:
            self._jobs, self._detail = jobs, detail
            self.jobsChanged.emit()


def main():
    QGuiApplication.setOrganizationName("Pianyu")
    QGuiApplication.setApplicationName("片语")
    app = QGuiApplication(sys.argv)
    app.setWindowIcon(QIcon(str(ASSETS / "ui" / "assets" / "pianyu.svg")))
    data = Path(os.environ.get("PIANYU_DATA_DIR") or QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppLocalDataLocation))
    data.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=data / "app.log", encoding="utf-8", level=logging.WARNING,
                        format="%(asctime)s %(levelname)s %(message)s")
    lock = QLockFile(str(data / "instance.lock"))
    if not lock.tryLock(0):
        return 2
    for browsers in (ASSETS / "browsers", APP / ".build" / "browsers"):
        if browsers.is_dir():
            os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(browsers))
            break
    controller = Controller(data)
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("appState", controller)
    qml = APP / "ui" / "Main.qml"
    if not qml.exists():
        qml = ASSETS / "ui" / "Main.qml"
    engine.load(QUrl.fromLocalFile(str(qml)))
    if not engine.rootObjects():
        return 1
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
