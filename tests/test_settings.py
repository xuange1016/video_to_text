from concurrent.futures import ThreadPoolExecutor
from threading import Event

from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest

import engine
import main
from engine import Transcriber
from main import Controller
from platforms import Cancelled


def wait_for(predicate):
    for _ in range(160):
        if predicate():
            return
        QTest.qWait(20)
    raise AssertionError("settings operation did not finish")


def test_directory_check_and_save_reject_invalid_locations(tmp_path, monkeypatch):
    app = QGuiApplication.instance() or QGuiApplication([])
    controller = Controller(tmp_path)
    original = controller.settings.copy()
    missing = tmp_path / "新目录"

    controller.checkFolder(str(missing))
    wait_for(lambda: controller.folderCheck["status"] == "invalid")
    assert "不存在" in controller.folderCheck["message"] and not missing.exists()

    target = tmp_path / "本地文件.txt"
    target.write_text("内容", encoding="utf-8")
    controller.saveSettings(str(target), "small", "srt", 2)
    wait_for(lambda: controller.folderCheck["status"] != "checking")
    assert controller.folderCheck["status"] == "invalid"
    assert "指向文件" in controller.folderCheck["message"]
    assert controller.settings == original

    controller.saveSettings('"' + str(missing) + '"', "small", "srt", 2)
    wait_for(lambda: controller.folderCheck["status"] == "valid")
    assert controller.settings["root"] == str(missing)
    assert missing.is_dir() and not list(missing.iterdir())

    def deny(*_args, **_kwargs):
        raise PermissionError

    monkeypatch.setattr(main, "check_output", deny)
    controller.checkFolder(str(missing))
    wait_for(lambda: controller.folderCheck["status"] == "invalid")
    assert "不可写" in controller.folderCheck["message"]
    app.processEvents()


def test_small_download_status_progress_cancel_and_retry(tmp_path, monkeypatch):
    app = QGuiApplication.instance() or QGuiApplication([])
    controller = Controller(tmp_path)
    finish = Event()
    cached = Event()
    monkeypatch.setattr(main, "model_path", lambda name, root: root if name == "small" and cached.is_set() else None)

    def transfer(name, report, cancelled):
        assert name == "small"
        report("model", .42)
        assert finish.wait(5)
        if cancelled():
            raise Cancelled
        cached.set()
        return tmp_path

    monkeypatch.setattr(controller.transcriber, "ensure_model", transfer)
    controller.downloadSmall()
    wait_for(lambda: controller.smallDownload["progress"] == 42)
    controller.cancelSmallDownload()
    finish.set()
    wait_for(lambda: controller.smallDownload["status"] == "cancelled")
    assert not controller.modelsInfo["small"]["ready"] and not controller.busy
    controller.downloadSmall()
    wait_for(lambda: controller.smallDownload["status"] == "ready")
    assert controller.modelsInfo["small"]["ready"] and controller.smallDownload["progress"] == 100
    app.processEvents()


def test_concurrent_model_requests_share_one_download(tmp_path, monkeypatch):
    transcriber = Transcriber(tmp_path)
    entered, release, waiting, cached = Event(), Event(), Event(), Event()
    calls = []
    monkeypatch.setattr(engine, "model_path", lambda _name, root: root if cached.is_set() else None)

    def download(name, _report, _cancel):
        calls.append(name)
        entered.set()
        assert release.wait(5)
        cached.set()
        return tmp_path

    monkeypatch.setattr(transcriber, "download_model", download)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(transcriber.ensure_model, "small", lambda *_: None, lambda: False)
        try:
            assert entered.wait(5)
            second = pool.submit(transcriber.ensure_model, "small", lambda *_: waiting.set(), lambda: False)
            assert waiting.wait(5)
        finally:
            release.set()
        assert first.result(5) == second.result(5) == tmp_path
    assert calls == ["small"]
