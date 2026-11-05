"""Local transcription, timed text, streaming audio extraction and export."""
from __future__ import annotations

import collections
import io
import json
import os
import queue
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

import requests
from opencc import OpenCC

from platforms import Cancelled, MediaError, NO_WINDOW


MODEL_SIZES = {"small": "约 480 MB", "medium": "约 1.5 GB"}
MODEL_FILES = ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt")
MINIMUM_MODEL_BYTES = {"small": 400_000_000, "medium": 1_300_000_000}
MAX_PARALLEL = 3
EXPORT_LOCK = threading.Lock()
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
TIMECODE = re.compile(r"(?:(\d+):)?(\d{2}):(\d{2})[,.](\d{3})")
TIMERANGE = re.compile(r"(?m)^(" + TIMECODE.pattern + r")\s+-->\s+(" + TIMECODE.pattern + r")[^\r\n]*")
INVALID_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
RESERVED_NAME = re.compile(r"^(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)", re.I)


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    text: str


def timestamp(value):
    ms = max(0, round(value * 1000))
    minutes, ms = divmod(ms, 60_000)
    hours, minutes = divmod(minutes, 60)
    seconds, ms = divmod(ms, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{ms:03}"


def seconds(code):
    h, m, s, ms = TIMECODE.fullmatch(code).groups()
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def parse_subtitle(body):
    if not isinstance(body, str) or not body.strip():
        return []
    body = body.replace("\r\n", "\n").lstrip("\ufeff")
    if body.lstrip().startswith(("{", "[")):
        try:
            data = json.loads(body)
            rows = data if isinstance(data, list) else data.get("body", [])
            segments = [Segment(float(x["from"]), float(x["to"]), str(x["content"]).strip()) for x in rows]
            return segments if valid_segments(segments) else []
        except (ValueError, TypeError, KeyError):
            return []
    segments = []
    for block in re.split(r"\n\s*\n", body):
        match = TIMERANGE.search(block)
        if match:
            text = re.sub(r"</?(?:c(?:\.[^>]*)?|i|b|u|v|ruby|rt)(?: [^>]*)?>", "", block[match.end():])
            start, end = seconds(match.group(1)), seconds(match.group(6))
            segments.append(Segment(start, end, " ".join(text.split())))
    return segments if valid_segments(segments) else []


def valid_segments(segments):
    return bool(segments) and all(
        0 <= item.start < item.end < 86400 * 14 and item.text.strip() for item in segments
    )


# Paragraph rules adapted from mumuxiaya/video2text (MIT, see THIRD_PARTY.md).
def plain_text(segments):
    paragraphs, buffer, previous = [], "", None
    for seg in segments:
        if buffer and previous is not None and seg.start - previous >= 1.5:
            paragraphs.append(buffer)
            buffer = ""
        separator = " " if buffer and re.search(r"[a-zA-Z0-9]$", buffer) and re.match(r"[a-zA-Z0-9]", seg.text) else ""
        buffer += separator + seg.text
        if buffer[-1] in "。！？!?…" or len(buffer) >= 70:
            paragraphs.append(buffer)
            buffer = ""
        previous = seg.end
    if buffer:
        paragraphs.append(buffer)
    return "\n".join(paragraphs)


def subtitles(segments):
    return "\n".join(f"{i}\n{timestamp(s.start)} --> {timestamp(s.end)}\n{s.text}\n"
                     for i, s in enumerate(segments, 1))


def result_payload(segments, source):
    return {"source": source, "segments": [asdict(item) for item in segments]}


def result_text(result):
    return plain_text([Segment(**segment) for segment in result["segments"]])


def model_path(name, models):
    local = models / f"faster-whisper-{name}"
    shared = Path.home() / ".workbuddy" / "cache" / "whisper-models" / local.name
    for candidate in (local, shared):
        if all((candidate / filename).is_file() for filename in MODEL_FILES):
            if (candidate / "model.bin").stat().st_size >= MINIMUM_MODEL_BYTES[name]:
                return candidate
    return None


@contextmanager
def model_access(lock, stage, report, cancel):
    if not lock.acquire(blocking=False):
        report(stage, None)
        while not lock.acquire(timeout=.2):
            if cancel():
                raise Cancelled
    try:
        if cancel():
            raise Cancelled
        yield
    finally:
        lock.release()


class Transcriber:
    def __init__(self, models):
        self.models = models
        self.model = None
        self.name = None
        self.cc = OpenCC("t2s")
        self._model_lock = threading.Lock()
        self._download_locks = {name: threading.Lock() for name in MODEL_SIZES}
        self._text_lock = threading.Lock()

    def normalize(self, segments):
        with self._text_lock:
            result = [Segment(s.start, s.end, self.cc.convert(s.text.strip())) for s in segments]
        return sorted(result, key=lambda s: (s.start, s.end))

    def read_model(self, name, report, cancel):
        from faster_whisper import WhisperModel

        try:
            path = self.ensure_model(name, report, cancel)
            with model_access(self._model_lock, "loading", report, cancel):
                if self.name == name and self.model:
                    return self.model
                self.name, self.model = None, None
                report("loading", None)
                cpu_threads = max(1, min(4, (os.cpu_count() or 1) // MAX_PARALLEL))
                self.model = WhisperModel(str(path), device="cpu", compute_type="int8", local_files_only=True,
                                          num_workers=MAX_PARALLEL, cpu_threads=cpu_threads)
                self.name = name
                return self.model
        except Cancelled:
            raise
        except Exception as exc:
            raise MediaError("model") from exc

    def ensure_model(self, name, report, cancel):
        with model_access(self._download_locks[name], "model", report, cancel):
            return model_path(name, self.models) or self.download_model(name, report, cancel)

    def download_model(self, name, report, cancel):
        from huggingface_hub import snapshot_download
        from tqdm.auto import tqdm

        class DownloadProgress(tqdm):
            def __init__(self, *args, **kwargs):
                kwargs.pop("name", None)
                kwargs["disable"] = False
                # A windowed Windows executable has no stderr for tqdm.close().
                kwargs["file"] = io.StringIO()
                super().__init__(*args, **kwargs)

            def update(self, n=1):
                if cancel():
                    raise Cancelled
                return super().update(n)

            def display(self, msg=None, pos=None):
                # snapshot_download reports each file separately. Only show the weights transfer.
                if not cancel() and self.unit == "B" and self.total and self.total >= MINIMUM_MODEL_BYTES[name]:
                    report("model", min(0.99, self.n / self.total))

        path = self.models / f"faster-whisper-{name}"
        for endpoint in ("https://huggingface.co", "https://hf-mirror.com"):
            if cancel():
                raise Cancelled
            report("model", None)
            try:
                snapshot_download(
                    repo_id=f"Systran/faster-whisper-{name}", local_dir=path, endpoint=endpoint,
                    allow_patterns=list(MODEL_FILES), max_workers=1, etag_timeout=8, tqdm_class=DownloadProgress,
                )
                if model_path(name, self.models):
                    return path
            except Cancelled:
                raise
            except Exception:
                if endpoint.endswith("hf-mirror.com"):
                    raise
        raise MediaError("model")

    def transcribe(self, path, name, report, cancel):
        model = self.read_model(name, report, cancel)
        report("transcribing", None)
        segments = []
        try:
            generated, info = model.transcribe(
                str(path), language="zh", beam_size=5, vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500},
                initial_prompt="以下是普通话口播内容，请输出简体中文，并加上正确的标点符号。",
                condition_on_previous_text=False,
            )
            for seg in generated:
                if cancel():
                    raise Cancelled
                if seg.text.strip() and 0 <= seg.start < seg.end:
                    segments.append(Segment(seg.start, seg.end, seg.text))
                if info.duration:
                    report("transcribing", min(.99, seg.end / info.duration))
        except Cancelled:
            raise
        except Exception as exc:
            if "does not contain any audio" in str(exc).lower():
                raise MediaError("no_audio") from exc
            raise MediaError("decode") from exc
        if not segments:
            raise MediaError("no_speech")
        return self.normalize(segments)


def prepare_audio(ffmpeg, sources, destination, duration, report, cancel):
    if not sources:
        raise MediaError("no_audio")
    last = "decode"
    # Alternatives often point to the same bytes. Limit retries across CDNs.
    for source in sources[:6]:
        if cancel():
            raise Cancelled
        try:
            report("audio", None)
            stream_audio(ffmpeg, source, destination, duration, report, cancel)
            return
        except MediaError as exc:
            destination.unlink(missing_ok=True)
            last = exc.code
    raise MediaError(last)


def stream_audio(ffmpeg, source, destination, duration, report, cancel):
    headers = {name: value for name, value in source.headers.items()
               if name.lower() in {"referer", "user-agent", "origin"}
               and "\r" not in str(value) and "\n" not in str(value)}
    try:
        with requests.get(source.url, headers=headers, stream=True, timeout=(7, 12)) as response:
            if response.status_code in (401, 403):
                raise MediaError("restricted")
            if response.status_code in (404, 410):
                raise MediaError("expired")
            response.raise_for_status()
            pipe_audio(ffmpeg, response, destination, duration, report, cancel)
    except requests.RequestException as exc:
        raise MediaError("network") from exc


def pipe_audio(ffmpeg, response, destination, duration, report, cancel):
    command = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
               "-map", "0:a:0", "-vn", "-sn", "-dn", "-ar", "16000", "-ac", "1",
               "-c:a", "pcm_s16le", "-progress", "pipe:1", "-y", str(destination)]
    messages = queue.Queue()
    errors = collections.deque(maxlen=10)
    feed_errors = []
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               creationflags=NO_WINDOW)

    def feed():
        try:
            for block in response.iter_content(64 * 1024):
                if cancel():
                    break
                process.stdin.write(block)
        except requests.RequestException as exc:
            feed_errors.append(exc)
        except OSError:
            pass
        finally:
            try:
                process.stdin.close()
            except OSError:
                pass

    def read_progress():
        for line in process.stdout:
            messages.put(line.decode("ascii", errors="replace"))

    writer = threading.Thread(target=feed, daemon=True)
    reader = threading.Thread(target=read_progress, daemon=True)
    error_reader = threading.Thread(target=lambda: errors.extend(process.stderr), daemon=True)
    writer.start()
    reader.start()
    error_reader.start()
    last_update = time.monotonic()
    try:
        while process.poll() is None:
            if cancel():
                raise Cancelled
            if time.monotonic() - last_update > 55:
                raise MediaError("network")
            try:
                line = messages.get(timeout=.2)
            except queue.Empty:
                continue
            last_update = time.monotonic()
            if duration and line.startswith("out_time_us="):
                value = line.partition("=")[2].strip()
                if value.isdigit():
                    report("audio", min(.99, int(value) / 1_000_000 / duration))
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        reader.join(timeout=1)
        error_reader.join(timeout=1)
        writer.join(timeout=1)
        process.stdout.close()
        process.stderr.close()
    if feed_errors:
        raise MediaError("network")
    if process.returncode or not destination.is_file() or destination.stat().st_size <= 100:
        err = b"".join(errors).decode("utf-8", errors="replace").lower()
        if "matches no streams" in err or "does not contain any stream" in err:
            raise MediaError("no_audio")
        if "403" in err or "401" in err:
            raise MediaError("restricted")
        if "404" in err or "410" in err:
            raise MediaError("expired")
        if "timed out" in err or "connection" in err:
            raise MediaError("network")
        raise MediaError("decode")


def process_media(platforms, transcriber, ffmpeg, media, options, temp, report, cancel):
    if cancel():
        raise Cancelled
    if media.source == "本地文件":
        path = Path(media.ref)
        if not path.is_file():
            raise MediaError("local")
        return result_payload(transcriber.transcribe(path, options["model"], report, cancel), "语音转写")

    force = options.get("force_audio", False)
    report("audio" if force else "subtitle", None)
    adapter = platforms.bilibili if media.source == "B 站" else platforms.douyin
    tracks, sources, duration = adapter(media, force)
    for track in tracks:
        if cancel():
            raise Cancelled
        try:
            segments = parse_subtitle(platforms.subtitle_body(track))
        except (requests.RequestException, ValueError, TypeError):
            continue
        if segments:
            return result_payload(transcriber.normalize(segments), "平台字幕")
    if not ffmpeg:
        raise MediaError("decode", "找不到随应用安装的 FFmpeg，请重新安装片语。")
    with tempfile.TemporaryDirectory(prefix="audio-", dir=temp) as scratch:
        audio = Path(scratch) / "source.wav"
        prepare_audio(ffmpeg, sources, audio, duration, report, cancel)
        return result_payload(transcriber.transcribe(audio, options["model"], report, cancel), "语音转写")


def safe_name(title):
    name = INVALID_NAME.sub("_", title).strip(" .")[:72].rstrip(" .") or "未命名素材"
    return "_" + name if RESERVED_NAME.match(name) else name


def output_path(root):
    root = root.strip()
    if root.startswith('"') and root.endswith('"'):
        root = root[1:-1]
    path = Path(root)
    if not path.is_absolute():
        raise ValueError("请填写保存目录的绝对路径。")
    if '\x00' in root:
        raise ValueError("保存目录包含无效字符。")
    return path


def check_output(root, *, create=True):
    path = output_path(root)
    if create:
        path.mkdir(parents=True, exist_ok=True)
    if not stat.S_ISDIR(path.stat().st_mode):
        raise NotADirectoryError(str(path))
    with tempfile.NamedTemporaryFile(prefix=".pianyu-check-", dir=path) as probe:
        probe.write(b"pianyu")
        probe.flush()
    return path


def export_contents(result, formats):
    segments = [Segment(**item) for item in result["segments"]]
    files = {}
    if formats in ("txt", "both"):
        files["文案.txt"] = plain_text(segments)
    if formats in ("srt", "both"):
        files["字幕.srt"] = subtitles(segments)
    return files


def published(destination, files):
    try:
        return destination.is_dir() and all(
            (destination / name).read_text(encoding="utf-8-sig") == text for name, text in files.items()
        )
    except OSError:
        return False


def export_result(root, title, suffix, files, reserved, remember):
    root = check_output(root)
    with EXPORT_LOCK:
        return publish_export(root, title, suffix, files, reserved, remember)


def publish_export(root, title, suffix, files, reserved, remember):
    if reserved:
        existing = Path(reserved)
        if existing.parent == root and published(existing, files):
            return str(existing)
    name = safe_name(title)
    destination = root / name
    if destination.exists():
        destination = root / f"{name}（{suffix}）"
        number = 2
        while destination.exists():
            destination = root / f"{name}（{suffix} {number}）"
            number += 1
    remember(str(destination))
    staged = Path(tempfile.mkdtemp(prefix=".pianyu-", dir=root))
    try:
        for filename, body in files.items():
            (staged / filename).write_text(body, encoding="utf-8-sig")
        os.rename(staged, destination)
    finally:
        if staged.exists():
            shutil.rmtree(staged)
    return str(destination)
