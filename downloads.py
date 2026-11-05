"""Cancellable platform video downloads and atomic, recoverable MP4 export."""
from __future__ import annotations

import collections
import hashlib
import json
import logging
import math
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
from contextlib import suppress
from pathlib import Path

import requests

from engine import EXPORT_LOCK, check_output, safe_name
from platforms import Cancelled, MediaError, NO_WINDOW, bilibili_error, network_error


QUALITIES = {"720", "1080", "best"}
MAX_PROCESS_LINE = 4 * 1024 * 1024


def stop_process(process):
    """Stop the process and its merger children before touching their files."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        with suppress(OSError, subprocess.SubprocessError):
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=5, creationflags=NO_WINDOW)
    if process.poll() is None:
        process.kill()
    process.wait(timeout=5)


def process_error(message, default):
    message = message.lower()
    if any(word in message for word in ("no space left", "not enough space", "permission denied", "access is denied")):
        return MediaError("video_save", "无法写入视频缓存。请检查权限和磁盘剩余空间。")
    if default == "platform":
        return bilibili_error(message)
    if "matches no streams" in message or "does not contain any stream" in message:
        return MediaError("no_video", "没有可保存的完整音视频流。请换一个来源重试。")
    return MediaError(default)


def run_process(command, cancel, on_line=None, *, idle_timeout=90, total_timeout=None, error="platform"):
    """Drain bounded output while checking cancellation even during quiet stages."""
    if cancel():
        raise Cancelled
    flags = NO_WINDOW | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, creationflags=flags)
    messages = queue.Queue(maxsize=64)
    done = threading.Event()
    stopping = threading.Event()
    output = collections.deque(maxlen=40)

    def read_output():
        try:
            while not stopping.is_set():
                line = process.stdout.readline(MAX_PROCESS_LINE + 1)
                if not line:
                    break
                while not stopping.is_set():
                    try:
                        messages.put(line, timeout=.2)
                        break
                    except queue.Full:
                        continue
        finally:
            done.set()

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    started = last_output = time.monotonic()
    try:
        while process.poll() is None or not done.is_set() or not messages.empty():
            if cancel():
                raise Cancelled
            now = time.monotonic()
            if now - last_output > idle_timeout or total_timeout is not None and now - started > total_timeout:
                raise MediaError("network", "获取视频超时。请检查网络后重试。")
            try:
                raw = messages.get(timeout=.2)
            except queue.Empty:
                continue
            if len(raw) > MAX_PROCESS_LINE:
                raise MediaError("platform")
            last_output = time.monotonic()
            line = raw.decode("utf-8", errors="replace").strip()
            output.append(line)
            if on_line:
                on_line(line)
        if process.wait(timeout=5):
            raise process_error("\n".join(output), error)
    finally:
        stopping.set()
        if process.poll() is None:
            stop_process(process)
        reader.join(timeout=2)
        process.stdout.close()
    return "\n".join(output)


def probe_video(ffprobe, path, cancel):
    if not ffprobe:
        raise MediaError("decode", "找不到 FFprobe，请检查片语的媒体工具文件。")
    text = run_process(
        [ffprobe, "-v", "error", "-count_packets", "-show_entries",
         "stream=codec_type,codec_name,width,height,nb_read_packets:format=duration", "-of", "json", str(path)],
        cancel, idle_timeout=90, error="decode",
    )
    try:
        info = json.loads(text)
        streams = info["streams"]
        video = next(s for s in streams if s.get("codec_type") == "video" and int(s.get("nb_read_packets") or 0) > 0)
        audio = next(s for s in streams if s.get("codec_type") == "audio" and int(s.get("nb_read_packets") or 0) > 0)
        duration = float(info.get("format", {}).get("duration") or 0)
        width, height = int(video["width"]), int(video["height"])
        if not math.isfinite(duration) or duration <= 0 or min(width, height) <= 0:
            raise ValueError
    except (ValueError, TypeError, KeyError, StopIteration) as exc:
        raise MediaError("no_video", "没有找到完整的画面和音轨。请换一个来源重试。") from exc
    return {"duration": duration, "width": width, "height": height,
            "video_codec": video.get("codec_name", ""), "audio_codec": audio.get("codec_name", "")}


def digest_file(path, cancel):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            if cancel():
                raise Cancelled
            digest.update(chunk)
    return digest.hexdigest()


def quality_matches(info, quality):
    return quality == "best" or min(info["width"], info["height"]) <= int(quality)


def checked_payload(ffprobe, path, quality, cancel):
    info = probe_video(ffprobe, path, cancel)
    if not quality_matches(info, quality):
        raise MediaError("no_quality", f"平台没有提供可用的 {quality}p 或更低清晰度视频。可选择最高可用后重试。")
    return {"kind": "video", "path": str(path), "bytes": path.stat().st_size,
            "sha256": digest_file(path, cancel), **info}


def fetch_file(session, source, destination, report, cancel):
    headers = {key: str(value) for key, value in source.headers.items()
               if key.lower() in {"referer", "user-agent", "origin"} and "\r" not in str(value) and "\n" not in str(value)}
    headers["Accept-Encoding"] = "identity"
    report("downloading", None)
    try:
        with session.get(source.url, headers=headers, stream=True, timeout=(7, 12)) as response:
            response.raise_for_status()
            total = int(response.headers.get("Content-Length") or 0)
            downloaded = 0
            with destination.open("wb") as output:
                for chunk in response.iter_content(256 * 1024):
                    if cancel():
                        raise Cancelled
                    if not chunk:
                        continue
                    output.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        report("downloading", min(.99, downloaded / total))
            if not downloaded or total and downloaded != total and not response.headers.get("Content-Encoding"):
                raise MediaError("network", "视频下载不完整。请检查网络后重试。")
    except requests.RequestException as exc:
        raise network_error(exc) from exc


def remux_video(ffmpeg, source, destination, info, report, cancel):
    report("muxing", None)

    def progress(line):
        if line.startswith("out_time_us="):
            value = line.partition("=")[2]
            if value.isdigit():
                report("muxing", min(.99, int(value) / 1_000_000 / info["duration"]))

    command = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(source),
               "-map", "0:v:0", "-map", "0:a:0", "-c", "copy", "-movflags", "+faststart"]
    if info["video_codec"] == "hevc":
        command.extend(["-tag:v", "hvc1"])
    run_process([*command, "-progress", "pipe:1", "-y", str(destination)], cancel, progress, error="decode")


def download_douyin(platforms, ffmpeg, media, quality, scratch, report, cancel):
    sources, _duration = platforms.douyin_video(media, quality)
    if cancel():
        raise Cancelled
    if not sources:
        raise MediaError("no_video", "平台没有提供可保存的视频。可稍后重试或改用其他链接。")
    last = MediaError("no_video", "没有找到可保存的完整视频。")
    source_file, ready = scratch / "source.bin", scratch / "ready.mp4"
    for source in sources[:6]:
        if cancel():
            raise Cancelled
        try:
            fetch_file(platforms.session, source, source_file, report, cancel)
            report("muxing", None)
            info = probe_video(platforms.ffprobe, source_file, cancel)
            if not quality_matches(info, quality):
                raise MediaError("no_quality", f"当前视频没有可用的 {quality}p 或更低清晰度来源。")
            remux_video(ffmpeg, source_file, ready, info, report, cancel)
            result = checked_payload(platforms.ffprobe, ready, quality, cancel)
            source_file.unlink(missing_ok=True)
            return result
        except MediaError as exc:
            last = exc
            source_file.unlink(missing_ok=True)
            ready.unlink(missing_ok=True)
    raise last


def download_bilibili(platforms, ffmpeg, media, quality, scratch, report, cancel):
    if not platforms.ytdlp:
        raise MediaError("platform", "找不到 yt-dlp，请检查片语的媒体工具文件。")
    sort = "res,vcodec:h264,acodec:aac" if quality == "best" else f"res:{quality},vcodec:h264,acodec:aac"
    common = [platforms.ytdlp, "--ignore-config", "--no-cache-dir", "--no-warnings", "--no-playlist",
              "--socket-timeout", "12", "--retries", "1", "--extractor-retries", "1", "--fragment-retries", "1",
              "--file-access-retries", "1", "--encoding", "utf-8", "--ffmpeg-location", str(Path(ffmpeg).parent),
              "-f", "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b", "-S", sort,
              "-o", str(scratch / "media.%(ext)s")]
    metadata = run_process([*common, "--dump-single-json", "--simulate", "--", media.ref],
                           cancel, total_timeout=80)
    try:
        info = json.loads(next(line for line in reversed(metadata.splitlines()) if line.startswith("{")))
    except (ValueError, StopIteration) as exc:
        raise MediaError("platform") from exc
    selected = info.get("requested_formats") or [info]
    dimensions = next((item for item in selected if item.get("vcodec") not in (None, "none")), {})
    if dimensions.get("width") and dimensions.get("height") and not quality_matches(dimensions, quality):
        raise MediaError("no_quality", f"平台没有提供可用的 {quality}p 或更低清晰度视频。可选择最高可用后重试。")
    info_file = scratch / "source.info.json"
    info_file.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
    sizes = {str(item.get("format_id")): item.get("filesize") for item in selected}
    received = {}
    report("downloading", None)

    def progress(line):
        if line.startswith("PIANYU_POST"):
            report("muxing", None)
        elif line.startswith("PIANYU "):
            fields = line.split()
            if len(fields) != 5:
                return
            _, key, _status, downloaded, total = fields
            if downloaded.isdigit():
                received[key] = int(downloaded)
            if total.isdigit():
                sizes[key] = int(total)
            if sizes and all(isinstance(size, (int, float)) and size > 0 for size in sizes.values()):
                fraction = sum(received.get(key, 0) for key in sizes) / sum(sizes.values())
                report("downloading", min(.99, fraction))

    run_process([*common, "--load-info-json", str(info_file), "--newline", "--progress", "--progress-delta", ".2",
                 "--progress-template", "download:PIANYU %(info.format_id)s %(progress.status)s %(progress.downloaded_bytes)s %(progress.total_bytes)s",
                 "--progress-template", "postprocess:PIANYU_POST %(progress.status)s", "--merge-output-format", "mp4",
                 "--no-write-info-json", "--no-write-thumbnail", "--no-write-subs", "--no-write-auto-subs"],
                cancel, progress)
    candidates = [path for path in scratch.glob("media.*") if path.suffix.lower() in {".mp4", ".mkv", ".webm", ".flv"}]
    if len(candidates) != 1:
        raise MediaError("no_video", "下载未生成完整视频文件。请重试。")
    source = candidates[0]
    report("muxing", None)
    details = probe_video(platforms.ffprobe, source, cancel)
    ready = scratch / "ready.mp4"
    remux_video(ffmpeg, source, ready, details, report, cancel)
    result = checked_payload(platforms.ffprobe, ready, quality, cancel)
    for path in scratch.iterdir():
        if path != ready and path.is_file():
            with suppress(OSError):
                path.unlink()
    return result


def download_video(platforms, ffmpeg, media, options, temp, report, cancel):
    if media.source == "本地文件":
        raise MediaError("video_local", "此素材已在本机。保存视频任务请选择网络视频。")
    if not ffmpeg:
        raise MediaError("decode", "找不到 FFmpeg，请检查片语的媒体工具文件。")
    quality = options.get("quality", "720")
    if quality not in QUALITIES:
        raise MediaError("invalid", "请选择 720p、1080p 或最高可用清晰度。")
    job = int(options["_job_id"])
    if job < 1:
        raise ValueError("A download requires a positive job id")
    temp = Path(temp).resolve()
    temp.mkdir(parents=True, exist_ok=True)
    scratch = temp / f"video-job-{job}"
    if scratch.resolve() != scratch:
        raise OSError("The video cache directory has been redirected")
    scratch.mkdir(exist_ok=True)
    ready = scratch / "ready.mp4"
    try:
        if cancel():
            raise Cancelled
        if ready.is_file():
            report("muxing", None)
            return checked_payload(platforms.ffprobe, ready, quality, cancel)
        report("media", None)
        if media.source == "B 站":
            return download_bilibili(platforms, ffmpeg, media, quality, scratch, report, cancel)
        if media.source == "抖音":
            return download_douyin(platforms, ffmpeg, media, quality, scratch, report, cancel)
        raise MediaError("invalid")
    except Exception:
        # Every file here belongs to this job. Completed results reach the store before export.
        if scratch.resolve() == scratch and scratch.parent == temp:
            with suppress(OSError):
                shutil.rmtree(scratch)
        raise


def cached_video(path, result, cancel):
    return path.is_file() and path.stat().st_size == result.get("bytes") and digest_file(path, cancel) == result.get("sha256")


def discard_cache(path):
    if path.name != "ready.mp4" or not re.fullmatch(r"video-job-[1-9]\d*", path.parent.name):
        return
    try:
        path.unlink(missing_ok=True)
        path.parent.rmdir()
    except OSError:
        logging.warning("Published video retained a cache file: %s", path)


def publish_video(root, title, suffix, result, reserved, remember, cancel):
    root = check_output(root)
    while not EXPORT_LOCK.acquire(timeout=.2):
        if cancel():
            raise Cancelled
    staged = None
    try:
        try:
            source = Path(result["path"])
        except (KeyError, TypeError, ValueError) as exc:
            raise MediaError("cache_missing", "视频缓存信息已丢失，请重试以重新下载。") from exc
        if cancel():
            raise Cancelled
        if reserved:
            existing = Path(reserved)
            if existing.parent == root and cached_video(existing / "视频.mp4", result, cancel):
                discard_cache(source)
                return str(existing)
        if not cached_video(source, result, cancel):
            raise MediaError("cache_missing", "视频缓存已丢失，请重试以重新下载。")
        name = safe_name(title)
        destination = root / name
        if destination.exists():
            destination = root / f"{name}（{suffix}）"
            number = 2
            while destination.exists():
                destination = root / f"{name}（{suffix} {number}）"
                number += 1
        remember(str(destination))
        staged = Path(tempfile.mkdtemp(prefix=".pianyu-video-", dir=root))
        exported = staged / "视频.mp4"
        digest = hashlib.sha256()
        with source.open("rb") as incoming, exported.open("wb") as outgoing:
            for chunk in iter(lambda: incoming.read(1024 * 1024), b""):
                if cancel():
                    raise Cancelled
                outgoing.write(chunk)
                digest.update(chunk)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        if digest.hexdigest() != result["sha256"]:
            raise MediaError("cache_missing", "视频缓存发生变化，请重试以重新下载。")
        if cancel():
            raise Cancelled
        os.rename(staged, destination)
        discard_cache(source)
        return str(destination)
    finally:
        try:
            if staged and staged.exists():
                shutil.rmtree(staged)
        except OSError:
            logging.warning("Could not remove an incomplete video export directory: %s", staged)
        finally:
            EXPORT_LOCK.release()
