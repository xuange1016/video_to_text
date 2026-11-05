import json
import os
from pathlib import Path
from threading import Event

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

from PySide6.QtCore import QMimeData, QObject, QPointF, QUrl, Qt
from PySide6.QtGui import QDesktopServices, QDragEnterEvent, QDropEvent, QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtTest import QTest

from engine import Segment
from main import Controller
from platforms import Media


def test_click_through_import_selection_and_export(tmp_path, monkeypatch):
    app = QGuiApplication.instance() or QGuiApplication([])
    controller = Controller(tmp_path)
    source = tmp_path / "测试视频标题很长以便检查队列里的自动简称以及原始文件名是否保留完整.mp4"
    source.touch()
    removed_source = tmp_path / "待移除.mp4"
    removed_source.touch()
    later_source = tmp_path / "稍后转写.mp4"
    later_source.touch()
    advance = Event()
    complete = Event()

    def transcribe(path, _model, report, _cancel):
        report("transcribing", 0.25)
        if path == source:
            advance.wait(timeout=6)
            report("transcribing", 0.5)
            complete.wait(timeout=6)
        return [Segment(0, 2, "桌面流程测试。")]

    monkeypatch.setattr(controller.platforms, "duration", lambda *_: 2)
    monkeypatch.setattr(controller.transcriber, "transcribe", transcribe)
    controller.saveSettings(str(tmp_path / "导出"), "medium", "both")
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda errors: warnings.extend(error.toString() for error in errors))
    engine.rootContext().setContextProperty("appState", controller)
    engine.load(QUrl.fromLocalFile(str(Path(__file__).resolve().parents[1] / "ui/Main.qml")))
    window = engine.rootObjects()[0]
    assert isinstance(window, QQuickWindow)

    def find(name):
        def scan(item):
            if item.objectName() == name:
                return item
            return next((match for child in item.childItems() if (match := scan(child))), None)

        return window.findChild(QObject, name) or scan(window.contentItem())

    def click(name, modifiers=Qt.NoModifier):
        QTest.qWait(60)
        control = find(name)
        assert control and control.property("visible") and control.property("enabled"), name
        point = control.mapToScene(QPointF(control.width() / 2, control.height() / 2)).toPoint()
        QTest.mouseClick(window, Qt.LeftButton, modifiers, point)

    def wait(predicate):
        for _ in range(150):
            if predicate():
                return
            QTest.qWait(25)
        raise AssertionError("desktop transition timed out")

    snapshot_dir = os.environ.get("PIANYU_TEST_SCREENSHOTS")

    def snapshot(name):
        if snapshot_dir:
            directory = Path(snapshot_dir)
            directory.mkdir(parents=True, exist_ok=True)
            assert window.grabWindow().save(str(directory / name))

    QTest.qWait(200)
    wait(lambda: controller.folderCheck["status"] == "valid")
    snapshot("01-import.png")
    find("sourceInput").setProperty("text", f"{source}\n{removed_source}\n{later_source}")
    click("resolveMaterials")
    wait(lambda: controller.stage == 2)
    click("selectAllMaterials")
    assert controller.selectedCount == 3
    QTest.qWait(100)
    snapshot("02-select.png")
    click("submitBatch")
    wait(lambda: len(controller.waitingJobs) == 3)
    assert not controller.running
    chosen = next(job for job in controller.waitingJobs if job["locator"] == str(source))
    removed = next(job for job in controller.waitingJobs if job["locator"] == str(removed_source))
    later = next(job for job in controller.waitingJobs if job["locator"] == str(later_source))
    assert chosen["displayTitle"] != chosen["title"]
    snapshot("03-waiting.png")
    click(f"editWaiting_{chosen['id']}")
    find("waitingAlias").setProperty("text", "采访初稿")
    click("saveWaiting")
    assert controller.waitingJobs[0]["displayTitle"] == "采访初稿"
    click(f"removeWaiting_{removed['id']}")
    click("confirmRemoveWaiting")
    assert len(controller.waitingJobs) == 2
    assert removed_source.is_file()
    click(f"startWaiting_{chosen['id']}")
    wait(lambda: controller.activeJobs and controller.activeJobs[0]["progress"] == 0.25)
    assert [job["id"] for job in controller.waitingJobs] == [later["id"]]
    assert find("queueModel").property("count") == 2
    wait_edit = find(f"editWaiting_{later['id']}")
    wait_edit.forceActiveFocus()
    QTest.qWait(1600)
    advance.set()
    wait(lambda: controller.activeJobs and controller.activeJobs[0]["progress"] == 0.5)
    assert find(f"editWaiting_{later['id']}") is wait_edit
    assert wait_edit.hasActiveFocus()
    assert "50%" in find(f"activeStatus_{chosen['id']}").property("text")
    assert "剩余约" in find(f"activeStatus_{chosen['id']}").property("text")
    snapshot("04-running.png")
    window.resize(860, 620)
    QTest.qWait(100)
    snapshot("05-running-compact.png")
    complete.set()
    wait(lambda: not controller.running)
    click("startAllWaiting")
    wait(lambda: not controller.running and not controller.waitingJobs)
    click("navHistory")
    click(f"historyBatch_{chosen['batch']}")
    assert controller.detail["status"] == "done"
    assert controller.detail["text"] == "桌面流程测试。"
    assert {f.name for f in Path(controller.detail["destination"]).iterdir()} == {"文案.txt", "字幕.srt"}
    QTest.qWait(100)
    copy_button = find("copyResult")
    assert copy_button.mapToScene(QPointF(0, copy_button.height())).y() < window.height() - 20
    snapshot("06-result-compact.png")
    window.resize(1280, 800)
    QTest.qWait(100)
    snapshot("07-result.png")
    click("navSettings")
    click("defaultFormat")
    wait(lambda: find("defaultFormatOption0") and find("defaultFormatOption1"))
    option = find("defaultFormatOption0")
    point = option.mapToScene(QPointF(option.width() / 2, option.height() / 2)).toPoint()
    QTest.mouseMove(window, point)
    wait(lambda: option.property("hovered"))
    assert option.property("contentItem").property("color").name() == "#292d26"
    snapshot("08-format-hover.png")
    QTest.keyClick(window, Qt.Key_Escape)
    wait(lambda: not (row := find("defaultFormatOption0")) or not row.property("visible"))
    click("defaultFormat")
    QTest.keyClick(window, Qt.Key_Down)
    QTest.keyClick(window, Qt.Key_Return)
    wait(lambda: find("defaultFormat").property("currentIndex") == 1)
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(Path(url.toLocalFile())) or True)
    click("settingsOutput", Qt.ControlModifier)
    assert opened == [tmp_path / "导出"]
    click("checkOutput")
    wait(lambda: controller.folderCheck["status"] == "valid")
    find("parallelism").setProperty("currentIndex", 1)
    click("saveSettings")
    wait(lambda: controller.settings["parallelism"] == 2)
    assert controller.settings["format"] == "txt"
    QTest.qWait(180)
    snapshot("09-settings.png")
    download_complete = Event()
    cached = Event()
    monkeypatch.setattr("main.model_path", lambda name, root: root if name == "small" and cached.is_set() else None)

    def download_model(_name, report, _cancel):
        report("model", .42)
        assert download_complete.wait(5)
        cached.set()
        return tmp_path

    monkeypatch.setattr(controller.transcriber, "ensure_model", download_model)
    controller.stateChanged.emit()
    click("downloadSmall")
    wait(lambda: find("smallDownloadPercentage").property("text") == "42%")
    QTest.qWait(180)
    snapshot("10-downloading.png")
    download_complete.set()
    wait(lambda: controller.smallDownload["status"] == "ready")
    assert "可离线使用" in find("smallDownloadMessage").property("text")
    click("reducedMotion")
    assert controller.settings["reducedMotion"] is True
    assert not warnings, warnings
    window.close()
    app.processEvents()


def test_drag_drop_bulk_waiting_and_video_result(tmp_path, monkeypatch):
    app = QGuiApplication.instance() or QGuiApplication([])
    controller = Controller(tmp_path)
    local_file = tmp_path / "拖入的本地视频.mp4"
    local_file.touch()
    folder = tmp_path / "导入文件夹"
    folder.mkdir()
    options = {"root": str(tmp_path / "默认导出"), "model": "medium", "format": "both"}
    controller.saveSettings(options["root"], options["model"], options["format"])
    remote_one = Media("bilibili:BVtest:1", "B 站", "远程视频一", "https://www.bilibili.com/video/BVtest/")
    remote_two = Media("douyin:12345", "抖音", "远程视频二", "https://www.douyin.com/video/12345")
    local_media = Media("local:" + str(local_file), "本地文件", "拖入的本地视频", str(local_file))
    monkeypatch.setattr(controller.platforms, "resolve", lambda *_: ([remote_one, remote_two, local_media], 0))
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda errors: warnings.extend(error.toString() for error in errors))
    engine.rootContext().setContextProperty("appState", controller)
    engine.load(QUrl.fromLocalFile(str(Path(__file__).resolve().parents[1] / "ui/Main.qml")))
    window = engine.rootObjects()[0]

    def find(name):
        def scan(item):
            if item.objectName() == name:
                return item
            return next((match for child in item.childItems() if (match := scan(child))), None)

        return window.findChild(QObject, name) or scan(window.contentItem())

    def click(name):
        QTest.qWait(70)
        control = find(name)
        assert control and control.property("visible") and control.property("enabled"), name
        point = control.mapToScene(QPointF(control.width() / 2, control.height() / 2)).toPoint()
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)

    def wait(predicate):
        for _ in range(160):
            if predicate():
                return
            QTest.qWait(25)
        raise AssertionError("desktop transition timed out")

    def snapshot(name):
        if directory := os.environ.get("PIANYU_TEST_SCREENSHOTS"):
            Path(directory).mkdir(parents=True, exist_ok=True)
            assert window.grabWindow().save(str(Path(directory) / name))

    QTest.qWait(180)
    wait(lambda: controller.folderCheck["status"] == "valid")
    original = "https://www.bilibili.com/video/BVexisting/"
    find("sourceInput").setProperty("text", original)
    point = find("sourceInput").mapToScene(QPointF(80, 80)).toPoint()
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(local_file)), QUrl.fromLocalFile(str(folder))])
    entering = QDragEnterEvent(point, Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    assert app.sendEvent(window, entering) and entering.isAccepted()
    snapshot("11-drag-over.png")
    drop = QDropEvent(QPointF(point), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    app.sendEvent(window, drop)
    wait(lambda: str(local_file) in find("sourceInput").property("text"))
    assert original in find("sourceInput").property("text")
    assert controller.folders == [str(folder)]
    click("resolveMaterials")
    wait(lambda: controller.stage == 2)
    click("taskKind")
    QTest.keyClick(window, Qt.Key_Down)
    QTest.keyClick(window, Qt.Key_Return)
    wait(lambda: controller.importKind == "download")
    click("selectAllMaterials")
    assert controller.selectedCount == 2
    assert next(row for row in controller.inputs if row["source"] == "本地文件")["usable"] is False
    find("batchQuality").setProperty("currentIndex", 1)
    snapshot("12-confirm-download.png")
    click("submitBatch")
    wait(lambda: len(controller.waitingJobs) == 2)
    download_jobs = controller.waitingJobs[:]
    assert all(job["kind"] == "download" and job["quality"] == "1080" for job in download_jobs)
    controller.store.create([local_media], options | {"kind": "transcribe"}, waiting=True)
    controller.refresh()
    wait(lambda: find("queueModel").property("count") == 3)
    click("selectAllWaiting")
    click("bulkEditWaiting")
    new_root = str(tmp_path / "统一保存目录")
    find("bulkOutput").setProperty("text", new_root)
    find("bulkModel").setProperty("currentIndex", 1)
    find("bulkFormat").setProperty("currentIndex", 2)
    find("bulkQuality").setProperty("currentIndex", 3)
    snapshot("13-bulk-config.png")
    click("applyBulkWaiting")
    assert all(job["root"] == new_root for job in controller.waitingJobs)
    assert all(job["quality"] == "best" for job in controller.waitingJobs if job["kind"] == "download")
    text_job = next(job for job in controller.waitingJobs if job["kind"] != "download")
    assert text_job["model"] == "small" and text_job["format"] == "txt"
    click("selectAllWaiting")
    click(f"checkWaiting_{text_job['id']}")
    click("removeWaitingMany")
    click("confirmRemoveWaitingMany")
    assert local_file.is_file() and len(controller.waitingJobs) == 2
    assert window.property("selectedWaiting").toVariant() == []
    window.resize(860, 620)
    QTest.qWait(140)
    snapshot("14-compact-queue.png")
    chosen = download_jobs[0]
    directory = tmp_path / "已完成视频"
    directory.mkdir()
    video = directory / "视频.mp4"
    video.write_bytes(b"mock-video-result")
    result = {"kind": "video", "path": str(video), "bytes": 2048, "width": 1280, "height": 720, "duration": 5}
    controller.store.change(chosen["id"], status="done", destination=str(directory), result=json.dumps(result), progress=1)
    controller.refresh()
    click("navHistory")
    click(f"historyBatch_{chosen['batch']}")
    assert controller.detail["resultType"] == "video"
    assert not find("copyResult").property("visible")
    assert not find("resultViewSwitch").property("visible")
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(Path(url.toLocalFile())) or True)
    click("playResultVideo")
    assert opened == [video]
    play = find("playResultVideo")
    assert play.mapToScene(QPointF(0, play.height())).y() < window.height() - 20
    snapshot("15-video-result-compact.png")
    window.resize(1280, 800)
    QTest.qWait(140)
    snapshot("16-video-result.png")
    assert not warnings, warnings
    window.close()
    app.processEvents()
