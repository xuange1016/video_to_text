import json
from pathlib import Path

import pytest

import storage
from engine import Segment, Transcriber, parse_subtitle, result_payload, result_text, safe_name, timestamp
from platforms import Media, MediaError, Platforms
from storage import Store, run_batch


def test_mixed_import_and_canonical_dedup(tmp_path, monkeypatch):
    media = tmp_path / "空 格.mp4"
    media.touch()
    platform = Platforms()
    monkeypatch.setattr(platform, "get_json", lambda *_a, **_kw: {
        "code": 0, "data": {"bvid": "BV1example", "title": "样例",
                           "pages": [{"page": 1, "part": "第一集", "duration": 10},
                                     {"page": 2, "part": "第二集", "duration": 20}]},
    })
    raw = f'"{media}"\n复制分享 https://www.bilibili.com/video/BV1example/，快来看\n' \
          "https://www.bilibili.com/video/BV1example/?p=2\nhttps://example.org/invalid"
    items, repeated = platform.resolve(raw, [str(tmp_path)], lambda: False)
    assert repeated == 2
    assert [x.key.split(":")[-1] for x in items if x.source == "B 站"] == ["1", "2"]
    assert sum(x.source == "本地文件" for x in items) == 1
    assert sum(bool(x.error) for x in items) == 1


def test_caption_tracks_share_normalized_text_and_preserve_timestamps(tmp_path):
    subs = "WEBVTT\n\n00:00.000 --> 00:01.230\n繁體字幕\n\n00:01.500 --> 00:03.000\n測試完成。"
    segments = Transcriber(tmp_path).normalize(parse_subtitle(subs))
    assert [seg.text for seg in segments] == ["繁体字幕", "测试完成。"]
    assert timestamp(segments[0].end) == "00:00:01,230"
    assert not parse_subtitle("1\n00:00:03,000 --> 00:00:01,000\n无效")
    assert safe_name('CON.1') == "_CON.1"


def test_batch_continues_and_only_reexports_on_save_failure(tmp_path, monkeypatch):
    store = Store(tmp_path / "state.sqlite")
    jobs = [
        Media("local:a", "本地文件", "同名", "a"),
        Media("local:b", "本地文件", "同名", "b"),
        Media("local:c", "本地文件", "同名", "c"),
    ]
    target = tmp_path / "root"
    target.mkdir()
    calls = []
    original_export = storage.export_result
    fail_next = [True]

    def fake_process(_platforms, _transcriber, _ffmpeg, media, _options, _temp, report, _cancel):
        calls.append(media.ref)
        report("transcribing", .5)
        if media.ref == "a":
            raise MediaError("expired")
        return result_payload([Segment(0, 1, "测试。")], "语音转写")

    def one_save_error(*args, **kwargs):
        if fail_next[0]:
            fail_next[0] = False
            raise PermissionError
        return original_export(*args, **kwargs)

    monkeypatch.setattr(storage, "process_media", fake_process)
    monkeypatch.setattr(storage, "export_result", one_save_error)
    batch = store.create(jobs, {"root": str(target), "model": "medium", "format": "both"})

    def run(job=None, out=""):
        run_batch(store, batch, job, out, None, None, None, tmp_path, lambda: None, lambda: False)

    run()
    a, b, c = store.list_jobs(batch)
    assert [row["status"] for row in (a, b, c)] == ["failed", "save_pending", "done"]
    assert "已失效" in a["error"] and "保存失败" in b["error"]
    assert result_text(json.loads(store.item(b["id"])["result"])) == "测试。"
    run(b["id"], str(target))
    assert calls == ["a", "b", "c"]
    b = store.item(b["id"])
    assert b["status"] == "done" and b["destination"] != c["destination"]
    assert {f.name for f in Path(b["destination"]).iterdir()} == {"文案.txt", "字幕.srt"}


def test_resume_marks_active_jobs_and_preserves_existing_result(tmp_path):
    db = tmp_path / "state.sqlite"
    store = Store(db)
    batch = store.create([Media("local:a", "本地文件", "源", "a"),
                          Media("local:b", "本地文件", "源", "b")], {})
    a, b = [row["id"] for row in store.list_jobs(batch)]
    store.change(a, status="transcribing")
    store.change(b, status="saving", result=json.dumps(result_payload([Segment(0, 1, "测试")], "平台字幕")))
    assert [row["status"] for row in Store(db).list_jobs(batch)] == ["interrupted", "save_pending"]


@pytest.mark.parametrize("fmt,expected", [
    ("txt", {"文案.txt"}), ("srt", {"字幕.srt"}), ("both", {"文案.txt", "字幕.srt"}),
])
def test_formats_and_idempotent_publication(tmp_path, fmt, expected):
    from engine import export_contents, export_result

    files = export_contents(result_payload([Segment(0, 1, "正常。")], "语音转写"), fmt)
    destination = export_result(str(tmp_path), "导出", "id", files, "", lambda _: None)
    assert {f.name for f in Path(destination).iterdir()} == expected
    assert destination == export_result(str(tmp_path), "导出", "id", files, destination, lambda _: None)


def test_caption_short_circuits_audio_and_inference(tmp_path, monkeypatch):
    from engine import process_media

    platform = Platforms()
    monkeypatch.setattr(platform, "bilibili", lambda *_: (
        [{"data": "1\n00:00:00,000 --> 00:00:02,000\n繁體測試。"}], [], 2,
    ))
    transcriber = Transcriber(tmp_path)
    monkeypatch.setattr(transcriber, "transcribe", lambda *_: pytest.fail("speech model ran for usable captions"))
    result = process_media(platform, transcriber, None, Media("bilibili:BV1:1", "B 站", "字幕", ""),
                           {"model": "medium"}, tmp_path, lambda *_: None, lambda: False)
    assert result["source"] == "平台字幕" and result_text(result) == "繁体测试。"


def test_model_progress_closes_without_a_console(tmp_path, monkeypatch):
    import sys
    import huggingface_hub
    import engine

    def download(local_dir, tqdm_class, **_options):
        size = engine.MINIMUM_MODEL_BYTES["small"]
        with tqdm_class(total=size, unit="B", mininterval=0) as progress:
            progress.update(size)
        local_dir.mkdir()

    monkeypatch.setattr(huggingface_hub, "snapshot_download", download)
    monkeypatch.setattr(engine, "model_path", lambda name, root: root / f"faster-whisper-{name}")
    monkeypatch.setattr(sys, "stderr", None)
    reports = []
    model = Transcriber(tmp_path).download_model("small", lambda *event: reports.append(event), lambda: False)
    assert model.is_dir()
    assert ("model", .99) in reports
