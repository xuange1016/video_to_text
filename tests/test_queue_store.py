import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import storage
from engine import Segment, result_payload
from platforms import Media
from storage import Store, run_batch


def test_waiting_queue_survives_restart_and_exports_using_edited_options(tmp_path, monkeypatch):
    db = tmp_path / "state.sqlite3"
    store = Store(db)
    source = tmp_path / "源.mp4"
    source.touch()
    original_title = "这是需要保留完整来源标题并使用原始名称导出的测试素材标题"
    media = Media("local:source", "本地文件", original_title, str(source), 3)
    batch = store.create([media], {"root": str(tmp_path), "model": "medium", "format": "txt"}, waiting=True)
    queued = store.waiting_jobs()[0]
    job = queued["id"]
    assert queued["batch"] == batch and queued["status"] == "waiting"
    assert queued["title"] == original_title and queued["displayTitle"] == original_title[:24] + "…"
    assert queued["locator"] == str(source) and not store.jobs_to_run(batch) and not store.active_jobs()

    output = tmp_path / "修改后的目录"
    output.mkdir()
    assert store.edit_waiting(job, "  试用　片段  ", {"root": str(output), "format": "srt"})
    restarted = Store(db)
    assert restarted.waiting_jobs()[0]["displayTitle"] == "试用 片段"
    assert json.loads(restarted.item(job)["options"]) == {
        "root": str(output), "model": "medium", "format": "srt",
    }
    assert restarted.release_waiting(job) == [job]
    assert restarted.release_waiting(job) == []
    assert not restarted.edit_waiting(job, "错误名称", {}) and not restarted.delete_waiting(job)
    assert [item["id"] for item in restarted.active_jobs()] == [job]

    def fake_process(_platforms, _transcriber, _ffmpeg, media, options, _temp, report, _cancel):
        assert media.title == original_title and options["model"] == "medium"
        report("transcribing", .3)
        report("transcribing", .301)
        return result_payload([Segment(0, 1, "测试。")], "语音转写")

    monkeypatch.setattr(storage, "process_media", fake_process)
    samples = []
    run_batch(restarted, batch, job, "", None, None, None, tmp_path, lambda: None, lambda: False,
              observe=lambda *sample: samples.append(sample))
    assert samples == [(job, "transcribing", .3), (job, "saving", None)]
    item = restarted.item(job)
    assert item["status"] == "done" and not restarted.active_jobs()
    assert Path(item["destination"]) == output / original_title
    assert {file.name for file in Path(item["destination"]).iterdir()} == {"字幕.srt"}


def test_delete_waiting_removes_only_queue_metadata(tmp_path):
    store = Store(tmp_path / "state.sqlite3")
    source = tmp_path / "源.mp4"
    source.write_bytes(b"source")
    batch = store.create([Media("local:source", "本地文件", "源", str(source))], {}, waiting=True)
    job = store.list_jobs(batch)[0]["id"]
    assert store.delete_waiting(job)
    assert not store.delete_waiting(job) and not store.edit_waiting(job, "missing", {})
    assert not store.waiting_jobs() and not store.list_batches() and not store.release_waiting()
    assert source.read_bytes() == b"source"


def test_parallel_release_and_retry_claims_are_atomic(tmp_path):
    db = tmp_path / "state.sqlite3"
    store = Store(db)
    batch = store.create([Media(f"local:{key}", "本地文件", key, key) for key in "abc"], {}, waiting=True)
    ids = [row["id"] for row in store.waiting_jobs()]

    def claim_concurrently(operation):
        ready = Barrier(2)

        def claim():
            ready.wait(timeout=5)
            return operation()

        with ThreadPoolExecutor(2) as pool:
            claims = [pool.submit(claim), pool.submit(claim)]
            assert sorted(result.result() for result in claims) == [[], ids]

    claim_concurrently(store.release_waiting)
    assert [job["id"] for job in store.active_jobs()] == ids
    assert store.prepare_retry(batch) == []
    store.change(ids[-1], status="saving", progress=.5, target=str(tmp_path), result="{}")
    restarted = Store(db)
    assert [job["status"] for job in restarted.list_jobs(batch)] == ["interrupted", "interrupted", "save_pending"]
    claim_concurrently(lambda: restarted.prepare_retry(batch))
    claimed = restarted.item(ids[-1])
    assert claimed["status"] == "queued" and claimed["progress"] is None and claimed["error"] == ""
    assert claimed["result"] == "{}" and claimed["target"] == str(tmp_path)


def test_legacy_store_gets_queue_columns_without_losing_history(tmp_path):
    db = tmp_path / "state.sqlite3"
    store = Store(db)
    batch = store.create([Media("local:legacy", "本地文件", "已有历史", "legacy")], {"format": "txt"})
    job = store.list_jobs(batch)[0]["id"]
    store.change(job, status="done", result="{}")
    with sqlite3.connect(db) as connection:
        connection.execute("ALTER TABLE jobs DROP COLUMN alias")
        connection.execute("ALTER TABLE jobs DROP COLUMN job_options")

    upgraded = Store(db)
    assert upgraded.list_jobs(batch)[0]["displayTitle"] == "已有历史"
    assert upgraded.item(job)["result"] == "{}"
    assert json.loads(upgraded.item(job)["options"]) == {"format": "txt"}
