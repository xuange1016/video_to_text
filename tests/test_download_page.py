from threading import Event

from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest

from main import Controller
from platforms import Media


def wait_for(predicate):
    for _ in range(180):
        if predicate():
            return
        QTest.qWait(20)
    raise AssertionError("download page transition timed out")


def test_page_contexts_and_parse_completion_are_independent(tmp_path, monkeypatch):
    app = QGuiApplication.instance() or QGuiApplication([])
    controller = Controller(tmp_path)
    text_media = Media("local:original", "本地文件", "文案素材", "original.mp4")
    video_media = Media("douyin:123", "抖音", "下载素材", "https://www.douyin.com/video/123")
    controller._inputs = [text_media]
    controller._folders = [str(tmp_path)]
    controller._stage = 2
    controller.selectAll()
    controller.activateImport("download")
    assert controller.inputs == [] and controller.folders == [] and controller.stage == 1
    entered, finish = Event(), Event()

    def resolve(*_args):
        entered.set()
        assert finish.wait(5)
        return [video_media], 0

    monkeypatch.setattr(controller.platforms, "resolve", resolve)
    controller.resolve(video_media.ref)
    assert entered.wait(5)
    controller.activateImport("transcribe")
    assert controller.selectedCount == 1
    assert controller.inputs[0]["key"] == text_media.key and controller.stage == 2
    finish.set()
    wait_for(lambda: not controller.resolvingKind)
    assert controller.inputs[0]["key"] == text_media.key
    assert controller.selectedCount == 1 and controller.folders == [str(tmp_path)]
    controller.activateImport("download")
    assert controller.inputs[0]["key"] == video_media.key and controller.stage == 2
    assert controller.selectedCount == 0 and controller.folders == []
    controller.selectAll()
    controller.activateImport("transcribe")
    controller.activateImport("download")
    assert controller.selectedCount == 1
    app.processEvents()


def test_starting_download_batch_does_not_release_text_queue(tmp_path, monkeypatch):
    app = QGuiApplication.instance() or QGuiApplication([])
    controller = Controller(tmp_path)
    text_batch = controller.store.create([Media("local:text", "本地文件", "等候文案", "text.mp4")],
                                         {"root": str(tmp_path), "kind": "transcribe"}, waiting=True)
    text_job = controller.store.list_jobs(text_batch)[0]["id"]
    controller.activateImport("download")
    controller._inputs = [Media("douyin:123", "抖音", "下载", "https://www.douyin.com/video/123")]
    controller._stage = 2
    controller.selectAll()
    submitted = []

    def run_job(job, _batch, _stop):
        submitted.append(job)
        controller.store.change(job, status="done", progress=1)
        controller._jobFinished.emit(job, "")

    monkeypatch.setattr(controller, "run_job", run_job)
    batch = controller.enqueueDownloads(str(tmp_path), "720", True)
    assert batch and batch != text_batch
    wait_for(lambda: not controller.running)
    video = controller.downloadJobs[0]
    assert submitted == [video["id"]] and video["status"] == "done"
    assert controller.store.item(text_job)["status"] == "waiting"
    assert controller.inputs and controller.stage == 2 and controller.selectedCount == 0

    controller.store.change(video["id"], status="failed")
    controller.store.change(text_job, status="failed")
    controller.viewBatch(text_batch)
    controller.retryDownload(video["id"])
    wait_for(lambda: not controller.running)
    assert submitted == [video["id"], video["id"]]
    assert controller.store.item(text_job)["status"] == "failed"
    assert controller.downloadJobs[0]["status"] == "done"
    controller._pool.shutdown(wait=True)
    app.processEvents()
