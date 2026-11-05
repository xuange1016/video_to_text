import threading

from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest

from engine import Segment
from main import Controller


def wait_for(predicate):
    for _ in range(150):
        if predicate():
            return
        QTest.qWait(20)
    raise AssertionError("queue transition timed out")


def test_parallel_slots_allow_import_and_waiting_edits(tmp_path, monkeypatch):
    app = QGuiApplication.instance() or QGuiApplication([])
    controller = Controller(tmp_path)
    root = str(tmp_path / "导出")
    controller.saveSettings(root, "small", "both", 2)
    source_paths = [tmp_path / f"目录 {number}" / "同名口播.mp4" for number in range(4)]
    for path in source_paths:
        path.parent.mkdir()
        path.touch()
    monkeypatch.setattr(controller.platforms, "duration", lambda *_: 2)
    lock = threading.Lock()
    release = threading.Event()
    active = set()
    peak = 0

    def fake_transcribe(path, _model, report, cancel):
        nonlocal peak
        with lock:
            active.add(path)
            peak = max(peak, len(active))
        report("transcribing", .1)
        try:
            assert release.wait(8)
            return [Segment(0, 2, "口播测试。")]
        finally:
            with lock:
                active.remove(path)

    monkeypatch.setattr(controller.transcriber, "transcribe", fake_transcribe)

    def enqueue(paths):
        controller.resolve("\n".join(map(str, paths)))
        wait_for(lambda: controller.stage == 2)
        controller.selectAll()
        return controller.enqueue(root, "small", "both")

    try:
        assert enqueue(source_paths[:3])
        first, second, third = controller.waitingJobs
        assert controller.editWaiting(second["id"], "第二条", root, "small", "txt")
        controller.startWaiting(0)
        wait_for(lambda: len(active) == 2)
        assert controller.running
        assert controller.store.item(third["id"])["status"] == "queued"
        assert enqueue(source_paths[3:])
        assert len(controller.waitingJobs) == 1
        assert not enqueue(source_paths[3:])
        controller.removeWaiting(controller.waitingJobs[0]["id"])
        assert not controller.waitingJobs
        controller.cancelJob(third["id"])
    finally:
        release.set()
        wait_for(lambda: not controller.running)
        controller._pool.shutdown(wait=True)

    records = controller.store.list_jobs(first["batch"])
    assert peak == 2
    assert [row["status"] for row in records] == ["done", "done", "cancelled"]
    assert records[0]["destination"] != records[1]["destination"]
    assert records[1]["displayTitle"] == "第二条"
    assert "同名口播" in records[1]["destination"]
