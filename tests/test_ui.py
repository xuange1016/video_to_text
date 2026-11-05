import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

from PySide6.QtCore import QObject, QPointF, QUrl, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtTest import QTest

from engine import Segment
from main import Controller


def test_click_through_import_selection_and_export(tmp_path, monkeypatch):
    app = QGuiApplication.instance() or QGuiApplication([])
    controller = Controller(tmp_path)
    source = tmp_path / "测试视频.mp4"
    source.touch()
    monkeypatch.setattr(controller.platforms, "duration", lambda *_: 2)
    monkeypatch.setattr(controller.transcriber, "transcribe", lambda *_: [Segment(0, 2, "桌面流程测试。")])
    controller.saveSettings(str(tmp_path / "导出"), "medium", "both")
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda errors: warnings.extend(error.toString() for error in errors))
    engine.rootContext().setContextProperty("appState", controller)
    engine.load(QUrl.fromLocalFile(str(Path(__file__).resolve().parents[1] / "ui/Main.qml")))
    window = engine.rootObjects()[0]
    assert isinstance(window, QQuickWindow)

    def click(name):
        control = window.findChild(QObject, name)
        assert control and control.property("visible") and control.property("enabled"), name
        point = control.mapToScene(QPointF(control.width() / 2, control.height() / 2)).toPoint()
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)

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
    snapshot("01-import.png")
    window.findChild(QObject, "sourceInput").setProperty("text", str(source))
    click("resolveMaterials")
    wait(lambda: controller.stage == 2)
    click("selectAllMaterials")
    assert controller.selectedCount == 1
    QTest.qWait(100)
    snapshot("02-select.png")
    click("submitBatch")
    wait(lambda: controller.stage == 3 and not controller.busy)
    assert controller.detail["status"] == "done"
    assert controller.detail["text"] == "桌面流程测试。"
    assert {f.name for f in Path(controller.detail["destination"]).iterdir()} == {"文案.txt", "字幕.srt"}
    QTest.qWait(100)
    snapshot("03-result.png")
    assert not warnings, warnings
    window.close()
    app.processEvents()
