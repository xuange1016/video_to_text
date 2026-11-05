import os
from pathlib import Path
from threading import Event

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

from PySide6.QtCore import QObject, QPointF, QUrl, Qt
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtTest import QTest

from engine import Segment
from main import Controller


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
