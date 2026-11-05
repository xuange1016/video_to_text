import hashlib
import io
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import downloads
from platforms import AudioSource, Cancelled, Media, MediaError, Platforms


def payload(directory, data=b"completed video"):
    directory.mkdir(parents=True)
    path = directory / "ready.mp4"
    path.write_bytes(data)
    return {"kind": "video", "path": str(path), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def test_atomic_video_publication_recovers_after_cache_cleanup(tmp_path):
    result = payload(tmp_path / "cache" / "video-job-1")
    output = tmp_path / "export"
    remembered = []
    destination = downloads.publish_video(str(output), 'CON.1', "source", result, "", remembered.append, lambda: False)
    assert Path(destination) == output / "_CON.1"
    assert (Path(destination) / "视频.mp4").read_bytes() == b"completed video"
    assert remembered == [destination] and not Path(result["path"]).exists()
    assert destination == downloads.publish_video(str(output), "CON.1", "source", result, destination,
                                                  remembered.append, lambda: False)
    (Path(destination) / "视频.mp4").write_bytes(b"different file")
    with pytest.raises(MediaError) as error:
        downloads.publish_video(str(output), "CON.1", "source", result, destination, remembered.append, lambda: False)
    assert error.value.code == "cache_missing"


def test_failed_or_cancelled_export_keeps_completed_video(tmp_path, monkeypatch):
    result = payload(tmp_path / "cache" / "video-job-2", b"v" * (2 * 1024 * 1024))
    output = tmp_path / "export"
    original_rename = downloads.os.rename

    def fail_rename(*_args):
        raise PermissionError

    monkeypatch.setattr(downloads.os, "rename", fail_rename)
    with pytest.raises(PermissionError):
        downloads.publish_video(str(output), "视频", "source", result, "", lambda *_: None, lambda: False)
    assert Path(result["path"]).is_file() and not list(output.iterdir())

    monkeypatch.setattr(downloads.os, "rename", original_rename)
    monkeypatch.setattr(downloads, "digest_file", lambda *_: result["sha256"])
    checkpoints = []

    def cancel():
        checkpoints.append(True)
        return len(checkpoints) >= 3

    with pytest.raises(Cancelled):
        downloads.publish_video(str(output), "视频", "source", result, "", lambda *_: None, cancel)
    assert Path(result["path"]).is_file() and not list(output.iterdir())


def test_parallel_video_exports_keep_same_title_results_separate(tmp_path):
    results = [payload(tmp_path / "cache" / f"video-job-{number}", bytes([number]) * 500) for number in (1, 2)]

    def export(result):
        return downloads.publish_video(str(tmp_path / "export"), "同名", "source", result, "", lambda *_: None, lambda: False)

    with ThreadPoolExecutor(2) as pool:
        destinations = list(pool.map(export, results))
    assert len(set(destinations)) == 2
    assert {Path(path, "视频.mp4").read_bytes() for path in destinations} == {bytes([1]) * 500, bytes([2]) * 500}


def test_malformed_result_releases_the_shared_export_lock(tmp_path):
    with pytest.raises(MediaError) as error:
        downloads.publish_video(str(tmp_path / "export"), "视频", "source", {}, "", lambda *_: None, lambda: False)
    assert error.value.code == "cache_missing"
    assert downloads.EXPORT_LOCK.acquire(blocking=False)
    downloads.EXPORT_LOCK.release()


def test_cache_cleanup_failure_does_not_fail_published_video(tmp_path, monkeypatch):
    result = payload(tmp_path / "cache" / "video-job-1")
    source = Path(result["path"])
    original_unlink = Path.unlink

    def locked(path, *args, **kwargs):
        if path == source:
            raise PermissionError
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", locked)
    destination = downloads.publish_video(str(tmp_path / "export"), "视频", "source", result, "", lambda *_: None, lambda: False)
    assert source.is_file() and (Path(destination) / "视频.mp4").is_file()
    assert downloads.publish_video(str(tmp_path / "export"), "视频", "source", result, destination,
                                   lambda *_: None, lambda: False) == destination


def test_incomplete_export_cleanup_cannot_hold_the_export_lock(tmp_path, monkeypatch):
    result = payload(tmp_path / "cache" / "video-job-1")

    def deny(*_args, **_kwargs):
        raise PermissionError

    monkeypatch.setattr(downloads.os, "rename", deny)
    monkeypatch.setattr(downloads.shutil, "rmtree", deny)
    with pytest.raises(PermissionError):
        downloads.publish_video(str(tmp_path / "export"), "视频", "source", result, "", lambda *_: None, lambda: False)
    assert downloads.EXPORT_LOCK.acquire(blocking=False)
    downloads.EXPORT_LOCK.release()
    assert Path(result["path"]).is_file()


def test_video_cache_is_per_job_and_partial_cancel_is_cleaned(tmp_path, monkeypatch):
    platform = Platforms(ffprobe="ffprobe", ytdlp="yt-dlp")
    media = Media("bilibili:BV1:1", "B 站", "素材", "https://example.org/")
    other = tmp_path / "video-job-9"
    other.mkdir()
    (other / "retain.part").write_bytes(b"another job")

    def cancel_download(_platform, _ffmpeg, _media, _quality, scratch, _report, _cancel):
        (scratch / "media.part").write_bytes(b"partial")
        raise Cancelled

    monkeypatch.setattr(downloads, "download_bilibili", cancel_download)
    with pytest.raises(Cancelled):
        downloads.download_video(platform, "ffmpeg", media, {"_job_id": 3, "quality": "720"}, tmp_path,
                                 lambda *_: None, lambda: False)
    assert not (tmp_path / "video-job-3").exists() and (other / "retain.part").read_bytes() == b"another job"
    saved = payload(tmp_path / "video-job-3")
    monkeypatch.setattr(downloads, "checked_payload", lambda *_: saved)
    assert downloads.download_video(platform, "ffmpeg", media, {"_job_id": 3}, tmp_path, lambda *_: None, lambda: False) == saved


def test_bilibili_progress_combines_video_and_audio_bytes(tmp_path, monkeypatch):
    platform = Platforms(ffprobe="ffprobe", ytdlp="yt-dlp")
    media = Media("bilibili:BV1:1", "B 站", "原始标题", "https://example.org/")
    info = {"requested_formats": [
        {"format_id": "video", "filesize": 900, "width": 1280, "height": 720, "vcodec": "avc1"},
        {"format_id": "audio", "filesize": 100, "vcodec": "none", "acodec": "mp4a"},
    ]}
    commands, reports = [], []

    def run(command, _cancel, on_line=None, **_kwargs):
        commands.append(command)
        if "--simulate" in command:
            return json.dumps(info)
        for sample in ("PIANYU video downloading 450 900", "PIANYU video finished 900 900",
                       "PIANYU audio downloading 50 100", "PIANYU audio finished 100 100", "PIANYU_POST finished"):
            on_line(sample)
        (tmp_path / "media.mp4").write_bytes(b"merged")
        return ""

    details = {"duration": 20, "width": 1280, "height": 720, "video_codec": "h264", "audio_codec": "aac"}
    monkeypatch.setattr(downloads, "run_process", run)
    monkeypatch.setattr(downloads, "probe_video", lambda *_: details)
    monkeypatch.setattr(downloads, "remux_video", lambda _ff, _source, ready, *_: ready.write_bytes(b"ready video"))
    result = downloads.download_bilibili(platform, str(tmp_path / "tools" / "ffmpeg.exe"), media, "720", tmp_path,
                                         lambda *sample: reports.append(sample), lambda: False)
    assert [p for stage, p in reports if stage == "downloading"] == [None, .45, .9, .95, .99]
    assert ("muxing", None) in reports and result["height"] == 720 and result["sha256"]
    assert "--load-info-json" in commands[1] and "--ffmpeg-location" in commands[0]
    assert commands[0][commands[0].index("-o") + 1] == str(tmp_path / "media.%(ext)s")
    assert {path.name for path in tmp_path.iterdir()} == {"ready.mp4"}


def test_full_probe_rejects_audio_only_and_checks_portrait_quality(tmp_path, monkeypatch):
    command_seen = []

    def probe(command, *_args, **_kwargs):
        command_seen.append(command)
        return json.dumps({"streams": [{"codec_type": "audio", "codec_name": "aac", "nb_read_packets": "9"}],
                           "format": {"duration": "2"}})

    monkeypatch.setattr(downloads, "run_process", probe)
    with pytest.raises(MediaError) as error:
        downloads.probe_video("ffprobe", tmp_path / "source.bin", lambda: False)
    assert error.value.code == "no_video" and "-count_packets" in command_seen[0]
    assert downloads.quality_matches({"width": 720, "height": 1280}, "720")
    assert not downloads.quality_matches({"width": 1920, "height": 1080}, "720")


def test_cancellation_terminates_the_process_tree(tmp_path, monkeypatch):
    class Process:
        stdout = io.BytesIO(b"")
        alive = True

        def poll(self):
            return None if self.alive else -1

    process = Process()
    stopped = []
    monkeypatch.setattr(downloads.subprocess, "Popen", lambda *_args, **_kwargs: process)

    def stop(child):
        stopped.append(child)
        child.alive = False

    monkeypatch.setattr(downloads, "stop_process", stop)
    checkpoints = []

    def cancel():
        checkpoints.append(True)
        return len(checkpoints) >= 2

    with pytest.raises(Cancelled):
        downloads.run_process(["downloader"], cancel)
    assert stopped == [process] and not process.alive


def test_douyin_skips_audio_only_address_and_uses_mixed_video(tmp_path, monkeypatch):
    platform = Platforms(ffprobe="ffprobe", ytdlp="yt-dlp")
    sources = [AudioSource("https://example.org/audio", {}), AudioSource("https://example.org/mixed", {})]
    monkeypatch.setattr(platform, "douyin_video", lambda *_: (sources, 2))
    retrieved = []

    def fetch(_session, source, destination, _report, _cancel):
        retrieved.append(source.url)
        destination.write_text(source.url, encoding="utf-8")

    details = {"duration": 2, "width": 720, "height": 1280, "video_codec": "hevc", "audio_codec": "aac"}

    def probe(_ffprobe, path, _cancel):
        if path.name == "source.bin" and path.read_text(encoding="utf-8").endswith("audio"):
            raise MediaError("no_video", "Audio only")
        return details

    monkeypatch.setattr(downloads, "fetch_file", fetch)
    monkeypatch.setattr(downloads, "probe_video", probe)
    monkeypatch.setattr(downloads, "remux_video", lambda _ff, _source, ready, *_: ready.write_bytes(b"mixed video"))
    media = Media("douyin:1", "抖音", "素材", "https://example.org/")
    result = downloads.download_douyin(platform, "ffmpeg", media, "720", tmp_path, lambda *_: None, lambda: False)
    assert retrieved == [source.url for source in sources] and result["video_codec"] == "hevc"
    assert Path(result["path"]).read_bytes() == b"mixed video"
