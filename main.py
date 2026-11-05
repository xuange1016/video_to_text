"""Desktop entry point and the QML-to-Python controller."""
from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import threading
from pathlib import Path

from PySide6.QtCore import Property, QLockFile, QObject, QStandardPaths, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QFontDatabase, QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from engine import MODEL_SIZES, check_output, model_path, result_text, timestamp, Transcriber
from platforms import Cancelled, Media, Platforms
from storage import Store, run_batch


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
    feedback = Signal(str)
    _finished = Signal(str, object, str)
    _newBatch = Signal(int)
    _tick = Signal()

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
                                              "model": "medium", "format": "both"})
        self._inputs = []
        self._checked = set()
        self._duplicates = 0
        self._folders = []
        self._stage = 1
        self._batch = 0
        self._active = 0
        self._job = 0
        self._task = ""
        self._detail = {}
        self._jobs = []
        self._history = self.store.list_batches()
        self._stop = threading.Event()
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
        self._newBatch.connect(self.batch_ready)
        self._tick.connect(self.refresh)

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
        return [media.__dict__ | {"checked": media.key in self._checked} for media in self._inputs]

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
        return self._task

    @Property(bool, notify=stateChanged)
    def busy(self):
        return bool(self._task)

    @Property(bool, notify=stateChanged)
    def canCancel(self):
        return self._task == "batch" and bool(self._active) and self._active == self._batch

    @Property("QVariantList", notify=jobsChanged)
    def jobs(self):
        return self._jobs

    @Property("QVariantList", notify=jobsChanged)
    def history(self):
        return self._history

    @Property("QVariantMap", notify=jobsChanged)
    def detail(self):
        return self._detail

    @Property(int, notify=jobsChanged)
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
        self.start("resolve", lambda: self.platforms.resolve(text, folders, self._stop.is_set))

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
        if not self.busy:
            self._stage = 1
            self.stateChanged.emit()

    @Slot()
    def newBatch(self):
        if not self.busy:
            self._stage, self._batch, self._job = 1, 0, 0
            self._inputs, self._folders, self._checked, self._duplicates = [], [], set(), 0
            self.refresh()
            self.stateChanged.emit()

    @staticmethod
    def options(root, model, formats):
        if model not in MODEL_SIZES or formats not in {"txt", "srt", "both"} or not root.strip():
            raise ValueError("请填写保存目录，并选择模型和格式。")
        return {"root": root.strip(), "model": model, "format": formats}

    @Slot(str, str, str)
    def saveSettings(self, root, model, formats):
        try:
            options = self.options(root, model, formats)
            if not Path(options["root"]).is_absolute():
                raise ValueError("请填写保存目录的绝对路径。")
            self.store.save_settings(options)
        except (ValueError, OSError) as exc:
            self.feedback.emit(str(exc))
            return
        self._settings = options
        self.stateChanged.emit()
        self.feedback.emit("设置已保存。")

    @Slot(str, str, str)
    def submit(self, root, model, formats):
        items = [m for m in self._inputs if m.key in self._checked]
        if not items or self.busy:
            return
        try:
            options = self.options(root, model, formats)
        except ValueError as exc:
            self.feedback.emit(str(exc))
            return

        def work():
            try:
                check_output(options["root"])
            except OSError as exc:
                raise ValueError("保存目录不可写。请更换位置后再提取。") from exc
            batch = self.store.create(items, options)
            self._newBatch.emit(batch)
            self.run(batch)
        self.start("batch", work)

    @Slot(int)
    def openBatch(self, batch):
        self._batch, self._job, self._stage = batch, 0, 3
        self.refresh()
        self.stateChanged.emit()

    @Slot(int)
    def openJob(self, job):
        self._job = job
        self.refresh()

    @Slot(int, str)
    def retry(self, job, destination):
        batch = self._batch
        selected = job or None
        if not self.store.jobs_to_run(batch, selected):
            self.feedback.emit("当前没有需要重试的任务。")
            return

        def work():
            if destination:
                try:
                    check_output(destination)
                except OSError as exc:
                    raise ValueError("这个目录不可写。请选择另一个目录。") from exc
            self._newBatch.emit(batch)
            self.run(batch, selected, destination)
        self.start("batch", work)

    @Slot(int)
    def forceAudio(self, job):
        original = self.store.item(job)
        if not original:
            return
        media = Media(**json.loads(original["media"]))
        options = json.loads(original["options"])
        if original["target"]:
            options["root"] = original["target"]

        def work():
            batch = self.store.create([media], options, parent=job)
            self._newBatch.emit(batch)
            self.run(batch)
        self.start("batch", work)

    @Slot()
    def cancel(self):
        self._stop.set()
        self.feedback.emit("正在停止，已完成的结果会保留。")

    @Slot()
    def cancelAndQuit(self):
        self._exit = True
        self.cancel()

    @Slot(str)
    def copy(self, value):
        QGuiApplication.clipboard().setText(value)
        self.feedback.emit("文案已复制。")

    @Slot(str)
    def openFolder(self, path):
        if not Path(path).is_dir() or not QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
            self.feedback.emit("无法打开该目录。")

    def start(self, kind, work):
        if self.busy:
            return
        self._task = kind
        self._stop.clear()
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

    def run(self, batch, item=None, destination=""):
        run_batch(self.store, batch, item, destination, self.platforms, self.transcriber,
                  self.ffmpeg, self.temp, self._tick.emit, self._stop.is_set)

    @Slot(int)
    def batch_ready(self, batch):
        self._active = batch
        if self._batch != batch:
            self.openBatch(batch)
        self.stateChanged.emit()

    @Slot(str, object, str)
    def finish(self, kind, result, error):
        self._task, self._active = "", 0
        if kind == "resolve" and result is not None:
            self._inputs, self._duplicates = result
            self._checked.clear()
            self._stage = 2
        self.refresh()
        self.stateChanged.emit()
        if error:
            self.feedback.emit(error)
        if self._exit:
            QGuiApplication.quit()

    @Slot()
    def refresh(self):
        self._jobs = self.store.list_jobs(self._batch)
        self._history = self.store.list_batches()
        selected = next((m for m in self._jobs if m["id"] == self._job), None)
        if not selected and self._jobs:
            selected = next((m for m in self._jobs if m["hasResult"]), self._jobs[0])
            self._job = selected["id"]
        self._detail = dict(selected or {})
        if selected and selected["hasResult"]:
            record = json.loads(self.store.item(self._job)["result"])
            self._detail.update(transcriptSource=record["source"], text=result_text(record),
                                timeline=[item | {"stamp": timestamp(item["start"]).split(",")[0]}
                                          for item in record["segments"]])
        self.jobsChanged.emit()


def main():
    QGuiApplication.setOrganizationName("Pianyu")
    QGuiApplication.setApplicationName("片语")
    app = QGuiApplication(sys.argv)
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
