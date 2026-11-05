import hashlib
import json
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication

import storage
from main import Controller
from platforms import Media, Platforms
from storage import Store, run_batch


def test_video_save_can_resume_after_controller_restart(tmp_path, monkeypatch):
    app = QGuiApplication.instance() or QGuiApplication([])
    controller = Controller(tmp_path)
    store = controller.store
    media = Media("douyin:123", "抖音", "视频标题", "https://www.douyin.com/video/123", 3)
    batch = store.create([media], {"kind": "download", "quality": "720", "root": str(tmp_path / "导出")})
    job = store.list_jobs(batch)[0]["id"]
    cache = controller.media_cache / f"video-job-{job}"
    cache.mkdir()
    payload = cache / "ready.mp4"
    body = b"test-video-content"
    payload.write_bytes(body)
    result = {"kind": "video", "path": str(payload), "bytes": len(body),
              "sha256": hashlib.sha256(body).hexdigest(), "duration": 3, "width": 720, "height": 1280}
    calls = []

    def download(*_args):
        calls.append(True)
        return result

    original_publish = storage.publish_video

    def denied(*_args):
        raise PermissionError

    monkeypatch.setattr(storage, "download_video", download)
    monkeypatch.setattr(storage, "publish_video", denied)
    run_batch(store, batch, job, "", None, None, None, controller.temp, lambda: None, lambda: False,
              media_cache=controller.media_cache)
    assert store.item(job)["status"] == "save_pending"
    assert payload.read_bytes() == body
    assert "视频已下载" in store.item(job)["error"]

    restarted = Controller(tmp_path)
    assert payload.read_bytes() == body
    monkeypatch.setattr(storage, "publish_video", original_publish)
    run_batch(restarted.store, batch, job, "", None, None, None, restarted.temp, lambda: None, lambda: False,
              media_cache=restarted.media_cache)
    saved = restarted.store.item(job)
    assert saved["status"] == "done" and calls == [True]
    assert (Path(saved["destination"]) / "视频.mp4").read_bytes() == body
    restarted.viewBatch(batch)
    assert restarted.detail["resultType"] == "video"
    assert restarted.detail["outputFile"].endswith("视频.mp4")
    assert "text" not in restarted.detail
    app.processEvents()


def test_bulk_waiting_edit_preserves_kind_and_individual_options(tmp_path):
    store = Store(tmp_path / "state.sqlite3")
    media = Media("douyin:123", "抖音", "标题", "https://www.douyin.com/video/123")
    text_batch = store.create([media, media], {"root": str(tmp_path), "model": "medium", "format": "both"}, waiting=True)
    video_batch = store.create([media], {"kind": "download", "root": str(tmp_path), "quality": "720"}, waiting=True)
    a, b, video = store.waiting_jobs()
    store.edit_waiting(a["id"], "手工简称", {"model": "small", "format": "srt"})
    store.release_waiting(b["id"])
    ids = [a["id"], b["id"], video["id"]]
    target = tmp_path / "新目录"
    assert store.edit_waiting_many(ids, {"root": str(target), "quality": "1080"}) == 2
    a, video = store.waiting_jobs()
    assert a["model"] == "small" and a["format"] == "srt" and a["displayTitle"] == "手工简称"
    assert a["root"] == video["root"] == str(target)
    assert video["kind"] == "download" and video["quality"] == "1080"
    assert "quality" not in json.loads(store.item(a["id"])["options"])
    assert store.list_jobs(text_batch)[1]["root"] == str(tmp_path)
    assert store.delete_waiting_many(ids) == 2
    assert [row["id"] for row in store.list_jobs(text_batch)] == [b["id"]]
    assert not store.list_jobs(video_batch)


def test_drop_import_and_same_source_supports_both_task_kinds(tmp_path):
    app = QGuiApplication.instance() or QGuiApplication([])
    controller = Controller(tmp_path)
    local = tmp_path / "测试 空格%片段.mp4"
    local.touch()
    ignored = tmp_path / "图片.png"
    ignored.touch()
    urls = [QUrl.fromLocalFile(str(path)).toString() for path in (local, tmp_path, local, ignored)]
    dropped = controller.acceptDrop(urls, "ignored duplicate URL text")
    assert dropped["foldersAdded"] == 1 and dropped["rejected"] == 1
    assert str(local) in dropped["text"] and "ignored" not in dropped["text"]
    assert controller.folders == [str(tmp_path)]
    media = Media("douyin:123", "抖音", "标题", "https://www.douyin.com/video/123")
    controller._inputs = [media, Media("local:file", "本地文件", "本地", str(local))]
    controller.selectAll()
    assert controller.selectedCount == 2
    controller.setImportKind("download")
    assert controller.selectedCount == 1 and not controller.inputs[1]["usable"]
    assert controller.enqueue(str(tmp_path), "small", "both", "download", "720")
    controller._inputs = [media]
    controller.setImportKind("transcribe")
    controller.selectAll()
    assert controller.enqueue(str(tmp_path), "small", "both", "transcribe", "720")
    assert {row["kind"] for row in controller.waitingJobs} == {"download", "transcribe"}
    app.processEvents()


def test_douyin_quality_candidates_exclude_background_audio(tmp_path, monkeypatch):
    platform = Platforms()
    video = {"width": 1080, "height": 1920, "duration": 3000,
             "audio": {"url_list": ["https://a.douyinvod.com/music"]},
             "bit_rate": [
                 {"bit_rate": 400, "is_bytevc1": 1, "play_addr": {"width": 1080, "height": 1920, "url_list": ["https://a.douyinvod.com/high"]}},
                 {"bit_rate": 100, "is_bytevc1": 0, "play_addr": {"width": 720, "height": 1280, "url_list": ["https://a.douyinvod.com/low"]}},
             ]}
    monkeypatch.setattr(platform, "_douyin_item", lambda _: ({"video": video}, {}))
    media = Media("douyin:123", "抖音", "标题", "")
    sources, duration = platform.douyin_video(media, "720")
    assert [source.url for source in sources] == ["https://a.douyinvod.com/low"] and duration == 3
