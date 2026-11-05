"""Desktop entry point and the QML-to-Python controller."""
from __future__ import annotations

import json
import logging
import math
import os
import shutil
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import Property, QLockFile, QObject, QStandardPaths, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QFontDatabase, QGuiApplication, QIcon
from PySide6.QtQml import QQmlApplicationEngine

from engine import MAX_PARALLEL, MODEL_SIZES, check_output, model_path, output_path, result_text, timestamp, Transcriber
from platforms import Cancelled, EXTENSIONS, Media, MESSAGES, Platforms
from storage import KIND_LABELS, Store, compact_title, run_batch


APP = Path(__file__).resolve().parent
ASSETS = Path(sys.argv[0]).resolve().parent if getattr(sys, "frozen", False) or "__compiled__" in globals() else APP
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")


def binary(name):
    name += ".exe" if sys.platform == "win32" else ""
    for root in (ASSETS, APP):
        for path in (root / "tools" / name, root / ".build" / "tools" / name, root / ".build" / "release-tools" / name):
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
    folderCheckChanged = Signal()
    settingsSaved = Signal()
    smallDownloadChanged = Signal()
    feedback = Signal(str)
    _finished = Signal(str, object, str)
    _jobFinished = Signal(int, str)
    _folderFinished = Signal(str, object, str)
    _smallProgress = Signal(object)
    _smallFinished = Signal(str)

    def __init__(self, data):
        super().__init__()
        self.data = data
        self.temp = data / "scratch"
        if self.temp.is_dir():
            shutil.rmtree(self.temp, ignore_errors=True)
        self.temp.mkdir(exist_ok=True)
        self.models = data / "models"
        self.models.mkdir(exist_ok=True)
        self.media_cache = data / "media-cache"
        self.media_cache.mkdir(exist_ok=True)
        self.store = Store(data / "tasks.sqlite3")
        documents = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
        self._settings = self.store.settings({"root": str(Path(documents) / "片语导出"),
                                              "model": "medium", "format": "both", "parallelism": 1,
                                              "reducedMotion": False})
        self._folder_check = {"path": "", "status": "idle", "message": ""}
        self._small_download = {"status": "idle", "progress": None, "message": ""}
        self._small_stop = threading.Event()
        self._inputs = []
        self._import_kind = "transcribe"
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
        self._folderFinished.connect(self.folder_finished)
        self._smallProgress.connect(self.small_progress)
        self._smallFinished.connect(self.small_finished)

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

    @Property("QVariantMap", notify=folderCheckChanged)
    def folderCheck(self):
        return self._folder_check

    @Property("QVariantMap", notify=smallDownloadChanged)
    def smallDownload(self):
        return self._small_download

    @Property("QVariantList", notify=stateChanged)
    def folders(self):
        return self._folders

    @Property("QVariantList", notify=stateChanged)
    def inputs(self):
        return [media.__dict__ | {"checked": media.key in self._checked,
                                  "displayTitle": compact_title(media.title), "usable": self.import_usable(media),
                                  "unavailableReason": media.error or (
                                      "此文件已在本机，可选择提取文案。" if not self.import_usable(media) else "")}
                for media in self._inputs]

    @Property(str, notify=stateChanged)
    def importKind(self):
        return self._import_kind

    def import_usable(self, media):
        return not media.error and (self._import_kind != "download" or media.source != "本地文件")

    @Slot(str)
    def setImportKind(self, kind):
        if kind in KIND_LABELS:
            self._import_kind = kind
            self._checked.intersection_update(media.key for media in self._inputs if self.import_usable(media))
            self.stateChanged.emit()

    @Property("QVariantMap", notify=stateChanged)
    def counts(self):
        return {**{source: sum(1 for m in self._inputs if m.source == source and not m.error)
                   for source in ("抖音", "B 站", "本地文件")},
                "duplicates": self._duplicates, "invalid": sum(bool(m.error) for m in self._inputs),
                "unavailable": sum(not self.import_usable(m) for m in self._inputs)}

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
        return bool(self._task) or self.running or self._small_download["status"] in {"downloading", "stopping"}

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

    @Slot("QVariantList", str, result="QVariantMap")
    def acceptDrop(self, urls, text):
        if self._task:
            self.feedback.emit("正在解析，完成后可继续拖入素材。")
            return {"text": "", "foldersAdded": 0, "rejected": 0}
        entries, added, rejected = [], 0, 0
        seen = set()
        for value in urls:
            url = QUrl(str(value))
            if url.isLocalFile():
                try:
                    path = Path(url.toLocalFile()).resolve(strict=True)
                    identity = str(path).casefold()
                    if identity in seen:
                        continue
                    seen.add(identity)
                    if path.is_dir():
                        if identity not in {folder.casefold() for folder in self._folders}:
                            self._folders.append(str(path))
                            added += 1
                    elif path.is_file() and path.suffix.lower().lstrip(".") in EXTENSIONS:
                        entries.append('"' + str(path) + '"')
                    else:
                        rejected += 1
                except (OSError, ValueError):
                    rejected += 1
            elif url.scheme().lower() in {"http", "https"}:
                ref = url.toString()
                if ref not in seen:
                    entries.append(ref)
                    seen.add(ref)
            else:
                rejected += 1
        if not urls and text.strip():
            entries.append(text.strip())
        self.stateChanged.emit()
        total = len(entries) + added
        message = f"已接收 {total} 项，请点击解析素材。" if total else "请拖入视频、音频、文件夹或视频分享链接。"
        if rejected:
            message += f" 跳过了 {rejected} 项不支持或无法读取的内容。"
        self.feedback.emit(message)
        return {"text": "\n".join(entries), "foldersAdded": added, "rejected": rejected}

    @Slot(str)
    def resolve(self, text):
        if not text.strip() and not self._folders:
            self.feedback.emit("请粘贴链接或文件路径，或者选择文件夹。")
            return
        folders = self._folders[:]
        self.start("resolve", lambda: self.platforms.resolve(text, folders, self._parse_stop.is_set))

    @Slot(str, bool)
    def selectItem(self, key, checked):
        if key and (not checked or any(m.key == key and self.import_usable(m) for m in self._inputs)):
            (self._checked.add if checked else self._checked.discard)(key)
            self.stateChanged.emit()

    @Slot()
    def selectAll(self):
        available = {m.key for m in self._inputs if self.import_usable(m)}
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
        return {"root": str(output_path(root)), "model": model, "format": formats}

    @Slot(str, str, str)
    @Slot(str, str, str, int)
    def saveSettings(self, root, model, formats, parallelism=None):
        if self._folder_check["status"] == "checking":
            return
        try:
            options = self.options(root, model, formats)
            parallelism = self._settings["parallelism"] if parallelism is None else parallelism
            if not 1 <= parallelism <= MAX_PARALLEL:
                raise ValueError(f"同时处理数应为 1 至 {MAX_PARALLEL}。")
            options["parallelism"] = parallelism
        except ValueError as exc:
            self.folder_finished(root.strip(), None, str(exc))
            return
        self.begin_folder_check(root, options)

    @Slot(str)
    def checkFolder(self, root):
        self.begin_folder_check(root, None)

    def begin_folder_check(self, root, options):
        if self._exit or self._folder_check["status"] == "checking":
            return
        root = root.strip()
        self._folder_check = {"path": root, "status": "checking", "message": "正在测试目录写入…"}
        self.folderCheckChanged.emit()

        def validate():
            error = ""
            try:
                check_output(root, create=options is not None)
            except ValueError as exc:
                error = str(exc)
            except FileNotFoundError:
                error = "目录不存在或当前不可用。请选择已有目录，或保存以创建目录。"
            except (NotADirectoryError, FileExistsError):
                error = "该路径指向文件。请选择文件夹。"
            except PermissionError:
                error = "该目录不可写。请选择有写入权限的文件夹。"
            except OSError:
                error = "无法写入该目录。请检查路径、磁盘空间或网络连接。"
            self._folderFinished.emit(root, options, error)

        threading.Thread(target=validate, daemon=True, name="pianyu-folder").start()

    @Slot(str, object, str)
    def folder_finished(self, root, options, error):
        if self._exit:
            return
        if not error and options:
            options = self._settings | options
            try:
                self.store.save_settings(options)
            except sqlite3.Error:
                logging.exception("Could not save settings")
                error = "设置写入失败。请重试。"
        self._folder_check = {"path": options["root"] if options and not error else root,
                              "status": "invalid" if error else "valid",
                              "message": error or "目录可写，校验通过。"}
        self.folderCheckChanged.emit()
        if not error and options:
            self._settings = options
            self.pump()
            self.stateChanged.emit()
            self.settingsSaved.emit()
            self.feedback.emit("设置已保存。")

    @Slot(bool)
    def setReducedMotion(self, reduced):
        settings = self._settings | {"reducedMotion": reduced}
        try:
            self.store.save_settings(settings)
        except sqlite3.Error:
            self.feedback.emit("动效设置未保存。请重试。")
        else:
            self._settings = settings
        self.stateChanged.emit()

    @Slot()
    def downloadSmall(self):
        if self._exit or self._small_download["status"] in {"downloading", "stopping"}:
            return
        self._small_stop.clear()
        self._small_download = {"status": "downloading", "progress": None, "message": "正在准备下载…"}
        self.smallDownloadChanged.emit()
        self.stateChanged.emit()

        def download():
            progress_before = -1

            def report(_, progress):
                nonlocal progress_before
                percent = int(progress * 100) if progress is not None else None
                if percent != progress_before:
                    progress_before = percent
                    self._smallProgress.emit(percent)

            result = "ready"
            try:
                self.transcriber.ensure_model("small", report, self._small_stop.is_set)
            except Cancelled:
                result = "cancelled"
            except Exception:
                logging.exception("Could not predownload the small model")
                result = "failed"
            self._smallFinished.emit(result)

        threading.Thread(target=download, daemon=True, name="pianyu-small").start()

    @Slot()
    def cancelSmallDownload(self):
        if self._small_download["status"] == "downloading":
            self._small_stop.set()
            self._small_download = {**self._small_download, "status": "stopping", "message": "正在停止下载…"}
            self.smallDownloadChanged.emit()

    @Slot(object)
    def small_progress(self, percent):
        if self._small_download["status"] != "downloading":
            return
        self._small_download = {"status": "downloading", "progress": percent,
                                "message": "正在连接下载源…" if percent is None else "正在下载模型权重"}
        self.smallDownloadChanged.emit()

    @Slot(str)
    def small_finished(self, result):
        if model_path("small", self.models):
            result = "ready"
        messages = {"ready": "small 已就绪，可离线转写。", "cancelled": "下载已停止，可重新点击下载。",
                    "failed": "small 下载失败。请检查网络和磁盘空间后重试。"}
        self._small_download = {"status": result, "progress": 100 if result == "ready" else None,
                                "message": messages[result]}
        self.smallDownloadChanged.emit()
        self.stateChanged.emit()
        self.feedback.emit(messages[result])
        self.maybe_quit()

    @Slot(str, str, str)
    def submit(self, root, model, formats):
        batch = self.create_selection(root, model, formats, waiting=False)
        if batch:
            self.openBatch(batch)
            self.schedule(self.store.jobs_to_run(batch))

    @Slot(str, str, str, result=bool)
    @Slot(str, str, str, str, str, result=bool)
    def enqueue(self, root, model, formats, kind="transcribe", quality="720"):
        batch = self.create_selection(root, model, formats, waiting=True, kind=kind, quality=quality)
        if not batch:
            return False
        self.newBatch()
        self.feedback.emit("素材已加入等待队列。")
        return True

    def create_selection(self, root, model, formats, *, waiting, kind="transcribe", quality="720"):
        items = [m for m in self._inputs if m.key in self._checked and not m.error
                 and (kind != "download" or m.source != "本地文件")]
        if not items or self._task or self._exit:
            return None
        try:
            options = self.options(root, model, formats)
            if kind not in KIND_LABELS or quality not in {"720", "1080", "best"}:
                raise ValueError("请选择任务类型和视频清晰度。")
            options.update(kind=kind, quality=quality)
            if waiting:
                current = {(row["key"], row["kind"]) for row in self.store.waiting_jobs() + self.store.active_jobs()}
                items = [media for media in items if (media.key, kind) not in current]
                if not items:
                    self.feedback.emit("所选素材已在任务队列中。")
                    return None
            return self.store.create(items, options, waiting=waiting)
        except ValueError as exc:
            self.feedback.emit(str(exc))
            return None

    @Slot(int, str, str, str, str, result=bool)
    @Slot(int, str, str, str, str, str, result=bool)
    def editWaiting(self, job, alias, root, model, formats, quality="720"):
        try:
            options = self.options(root, model, formats)
            if quality not in {"720", "1080", "best"}:
                raise ValueError("请选择可用的视频清晰度。")
            options["quality"] = quality
            updated = self.store.edit_waiting(job, alias, options)
        except ValueError as exc:
            self.feedback.emit(str(exc))
            return False
        self.refresh()
        self.feedback.emit("任务已更新。" if updated else "任务已离开等待队列。")
        return updated

    @Slot("QVariantList", str, str, str, str, result=int)
    def bulkEditWaiting(self, jobs, root, model, formats, quality):
        try:
            patch = {}
            if root.strip():
                patch["root"] = str(output_path(root))
            if model:
                if model not in MODEL_SIZES:
                    raise ValueError("请选择可用的转写模型。")
                patch["model"] = model
            if formats:
                if formats not in {"txt", "srt", "both"}:
                    raise ValueError("请选择可用的导出格式。")
                patch["format"] = formats
            if quality:
                if quality not in {"720", "1080", "best"}:
                    raise ValueError("请选择可用的视频清晰度。")
                patch["quality"] = quality
            if not patch:
                raise ValueError("请先选择需要调整的配置。")
            ids = sorted({int(job) for job in jobs if int(job) > 0})
            count = self.store.edit_waiting_many(ids, patch)
        except (ValueError, TypeError) as exc:
            self.feedback.emit(str(exc))
            return 0
        self.refresh()
        self.feedback.emit(f"已更新 {count} 条等待任务。")
        return count

    @Slot("QVariantList", result=int)
    def removeWaitingMany(self, jobs):
        try:
            ids = sorted({int(job) for job in jobs if int(job) > 0})
        except (ValueError, TypeError):
            return 0
        count = self.store.delete_waiting_many(ids)
        self.refresh()
        self.feedback.emit(f"已移除 {count} 条等待任务。")
        return count

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
        if options.get("kind", "transcribe") != "transcribe":
            return
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
        self.cancelSmallDownload()
        for job in [*self._pending, *self._inflight]:
            self.stop_job(job)
        self.maybe_quit()

    @Slot(str)
    def copy(self, value):
        QGuiApplication.clipboard().setText(value)
        self.feedback.emit("文案已复制。")

    @Slot(str)
    def openFolder(self, path):
        try:
            folder = output_path(path)
            if folder.is_dir() and QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
                return
        except (ValueError, OSError):
            pass
        self.feedback.emit("无法打开目录。请先确认它存在且可访问。")

    @Slot(int)
    def openVideo(self, job):
        record = self.store.item(job)
        if not record or record["status"] != "done" or json.loads(record["options"]).get("kind") != "download":
            return
        target = Path(record["destination"]) / "视频.mp4"
        if not target.is_file() or not QDesktopServices.openUrl(QUrl.fromLocalFile(str(target))):
            self.feedback.emit("无法打开视频。请检查文件或选择可播放该格式的播放器。")

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
                          lambda: None, stop.is_set, observe=self.observe_progress, media_cache=self.media_cache)
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
                self.store.change(job, status="save_pending", progress=None, error="已取消，可补存结果。")
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
            if stage not in {"transcribing", "audio", "downloading"} or progress is None:
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
        active_changed = active != self._active_jobs
        waiting_changed = waiting != self._waiting
        history_changed = history != self._history
        self._active_jobs, self._waiting, self._history = active, waiting, history
        if active_changed:
            self.activeChanged.emit()
        if waiting_changed:
            self.queueChanged.emit()
        if history_changed:
            self.historyChanged.emit()
        selected = next((m for m in jobs if m["id"] == self._job), None)
        if not selected and jobs:
            selected = next((m for m in jobs if m["hasResult"]), jobs[0])
            self._job = selected["id"]
        detail = dict(selected or {})
        if selected and selected["hasResult"]:
            if selected["kind"] == "download":
                record = json.loads(self.store.item(self._job)["result"])
                detail.update(resultType="video", videoInfo=record, videoSize=record.get("bytes", 0),
                              duration=record.get("duration") or selected["duration"],
                              videoWidth=record.get("width", 0), videoHeight=record.get("height", 0),
                              outputFile=str(Path(selected["destination"]) / "视频.mp4") if selected["status"] == "done" else "")
            elif self._detail.get("id") == self._job and "text" in self._detail:
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
