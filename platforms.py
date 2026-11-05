"""Resolve supported sources, and open media only after a job is submitted."""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit

import requests


EXTENSIONS = set("mp4 mov mkv avi flv wmv m4v webm ts mp3 m4a wav aac flac ogg opus wma".split())
VIDEO_LINK = re.compile(r"https?://[^\s<>\"“”‘’\u3000，。；！？、）】]+", re.I)
WINDOWS_PATH = re.compile(
    r'"(?:[A-Z]:[\\/]|\\\\)[^"\r\n]+"|'
    r'(?:[A-Z]:[\\/]|\\\\)[^\r\n<>"]+?\.(?:' + "|".join(sorted(EXTENSIONS)) + r')(?=$|[\s,;，；])',
    re.I,
)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
MESSAGES = {
    "invalid": "无法识别该链接或文件路径。请检查输入。",
    "expired": "链接已失效或视频已删除。请重新复制分享链接。",
    "restricted": "来源限制访问。可导入你能够访问的本地文件。",
    "platform": "平台返回异常。可稍后重试，或改用本地文件。",
    "network": "网络请求失败。请检查网络后重试。",
    "local": "文件不存在或无法读取。请检查位置和访问权限。",
    "empty_folder": "文件夹内没有找到支持的音视频文件。",
    "no_audio": "此素材没有可用音轨。可检查源文件后重试。",
    "decode": "读取或解码音频失败。请检查素材或重新获取。",
    "no_speech": "有音轨，但没有识别到人声。",
    "model": "模型加载或下载失败。请检查网络和磁盘空间后重试。",
    "save": "文案已生成，保存失败。请更换目录或检查权限和磁盘空间。",
    "unexpected": "处理遇到异常。请重试该条素材。",
}


class MediaError(Exception):
    def __init__(self, code, message=None):
        self.code = code
        super().__init__(message or MESSAGES[code])


class Cancelled(Exception):
    pass


@dataclass(frozen=True)
class Media:
    key: str
    source: str
    title: str
    ref: str
    duration: float | None = None
    error: str = ""


@dataclass(frozen=True)
class AudioSource:
    url: str
    headers: dict[str, str]


def host_is(host, *domains):
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def extract_inputs(text):
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        urls = [m.group().rstrip(").,;!?") for m in VIDEO_LINK.finditer(line)]
        remainder = VIDEO_LINK.sub(" ", line)
        paths = [m.group().strip().strip('"') for m in WINDOWS_PATH.finditer(remainder)]
        yield from (urls + paths or [line.strip('"')])


def is_zh(code):
    return bool(re.match(r"^(?:ai[-_])?(?:zh|cmn|yue)(?:[-_]|$)", str(code), re.I))


def network_error(exc):
    if isinstance(exc, requests.HTTPError):
        if exc.response.status_code in (404, 410):
            return MediaError("expired")
        if exc.response.status_code in (401, 403):
            return MediaError("restricted")
    if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
        return MediaError("network")
    return MediaError("platform")


class Platforms:
    def __init__(self, ffprobe=None, ytdlp=None):
        self.ffprobe = ffprobe
        self.ytdlp = ytdlp
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": UA})

    def get_json(self, url, **kwargs):
        for attempt in range(2):
            try:
                with self.session.get(url, timeout=(7, 14), **kwargs) as response:
                    response.raise_for_status()
                    return response.json()
            except (requests.RequestException, ValueError) as exc:
                error = network_error(exc)
                if attempt or error.code != "network":
                    raise error from exc

    def expand_short_link(self, url, *allowed):
        for _ in range(6):
            with self.session.get(url, timeout=(7, 12), stream=True, allow_redirects=False) as response:
                if not response.is_redirect:
                    response.raise_for_status()
                    return url
                target = urljoin(url, response.headers["Location"])
            parsed = urlsplit(target)
            if parsed.scheme != "https" or not host_is(parsed.hostname or "", *allowed):
                raise MediaError("invalid")
            if re.search(r"/(?:video/|share/video/)(?:BV|av|\d)", parsed.path, re.I):
                return target
            url = target
        raise MediaError("expired")

    def duration(self, path):
        if not self.ffprobe:
            return None
        try:
            result = subprocess.run(
                [self.ffprobe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                capture_output=True, timeout=6, creationflags=NO_WINDOW, check=True,
            )
            return float(result.stdout)
        except (subprocess.SubprocessError, OSError, ValueError):
            return None

    def resolve_one(self, value):
        if not value.lower().startswith(("http://", "https://")):
            path = Path(value).expanduser()
            if not path.is_absolute():
                raise MediaError("invalid")
            path = path.resolve()
            if not path.is_file():
                raise MediaError("local")
            if path.suffix.lower()[1:] not in EXTENSIONS:
                raise MediaError("invalid", "不支持此文件格式。请选择音频或视频。")
            return [Media("local:" + str(path).casefold(), "本地文件", path.stem, str(path), self.duration(path))]

        parsed = urlsplit(value)
        host = parsed.hostname or ""
        if parsed.username or parsed.password or parsed.port:
            raise MediaError("invalid")
        if host_is(host, "douyin.com", "iesdouyin.com"):
            if host == "v.douyin.com":
                value = self.expand_short_link("https://" + parsed.netloc + parsed.path, "douyin.com", "iesdouyin.com")
            match = re.search(r"/(?:share/)?video/(\d+)", urlsplit(value).path)
            if not match:
                raise MediaError("invalid")
            video_id = match[1]
            result = self.get_json(
                "https://open.douyin.com/api/douyin/v1/video/get_iframe_by_video",
                params={"video_id": video_id},
            )
            if result.get("err_no"):
                code = result["err_no"]
                raise MediaError("restricted" if code == 28003004 else "platform")
            title = (result.get("data") or {}).get("video_title") or f"抖音 {video_id}"
            return [Media(f"douyin:{video_id}", "抖音", title, f"https://www.douyin.com/video/{video_id}")]

        if host_is(host, "bilibili.com", "b23.tv", "bili2233.cn"):
            if host_is(host, "b23.tv", "bili2233.cn"):
                value = self.expand_short_link("https://" + parsed.netloc + parsed.path, "bilibili.com", "b23.tv", "bili2233.cn")
            parsed = urlsplit(value)
            match = re.search(r"/video/(BV[a-zA-Z0-9]+|av\d+)", parsed.path, re.I)
            if not match:
                raise MediaError("invalid")
            video_id = match[1]
            params = {"aid": video_id[2:]} if video_id.lower().startswith("av") else {"bvid": video_id}
            response = self.get_json("https://api.bilibili.com/x/web-interface/view", params=params,
                                     headers={"Referer": "https://www.bilibili.com/"})
            if response.get("code") != 0:
                raise MediaError({-404: "expired", 62002: "expired", -403: "restricted",
                                  62012: "restricted"}.get(response.get("code"), "platform"))
            data = response["data"]
            pages = data.get("pages") or [{"page": 1, "duration": data.get("duration")}]
            part = parse_qs(parsed.query).get("p", [None])[0]
            chosen = [p for p in pages if part is None or str(p["page"]) == part]
            if not chosen:
                raise MediaError("invalid", "找不到链接中指定的 B 站分 P。")
            return [
                Media(f"bilibili:{data['bvid']}:{p['page']}", "B 站",
                      f"{data['title']} · P{p['page']} {p.get('part', '')}".strip() if len(pages) > 1 else data["title"],
                      f"https://www.bilibili.com/video/{data['bvid']}/?p={p['page']}", p.get("duration"))
                for p in chosen
            ]
        raise MediaError("invalid", "首版支持抖音、B 站和本地音视频文件。")

    def resolve(self, text, folders, cancelled):
        seen = set()
        items = []
        duplicates = 0

        def add(value):
            nonlocal duplicates
            if cancelled():
                raise Cancelled
            try:
                resolved = self.resolve_one(value)
            except (MediaError, OSError, ValueError, requests.RequestException) as exc:
                if isinstance(exc, requests.RequestException):
                    exc = network_error(exc)
                message = str(exc) if isinstance(exc, MediaError) else MESSAGES["local"]
                items.append(Media("", "未识别", value, value, error=message))
                return
            for item in resolved:
                if item.key in seen:
                    duplicates += 1
                else:
                    seen.add(item.key)
                    items.append(item)

        values = list(extract_inputs(text))
        unique = dict.fromkeys(values)
        for value in unique:
            add(value)
        duplicates += len(values) - len(unique)
        for value in folders:
            folder = Path(value)
            count = 0
            if folder.is_dir():
                for path in folder.rglob("*"):
                    if cancelled():
                        raise Cancelled
                    if path.suffix.lower()[1:] in EXTENSIONS and path.is_file():
                        add(str(path))
                        count += 1
            if not count:
                items.append(Media("", "本地文件", folder.name or value, value, error=MESSAGES[
                    "empty_folder" if folder.is_dir() else "local"]))
        return items, duplicates

    def bilibili(self, media, force_audio):
        if self.ytdlp:
            command = [self.ytdlp, "--ignore-config", "--no-cache-dir", "--no-warnings",
                       "--no-playlist", "--simulate", "--dump-single-json", "--socket-timeout", "12",
                       "--retries", "1", "--extractor-retries", "1", "--encoding", "utf-8"]
            if not force_audio:
                command.extend(["--write-subs", "--write-auto-subs"])
            try:
                completed = subprocess.run([*command, "--", media.ref], capture_output=True, timeout=65,
                                           encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
            except subprocess.TimeoutExpired as exc:
                raise MediaError("network") from exc
            if completed.returncode:
                raise bilibili_error(completed.stderr)
            try:
                info = json.loads(completed.stdout)
            except ValueError as exc:
                raise MediaError("platform") from exc
        else:
            info = bilibili_python(media, force_audio)

        tracks = [] if force_audio else [
            track for group in ("subtitles", "automatic_captions")
            for lang, tracks in (info.get(group) or {}).items() if is_zh(lang) for track in tracks
        ]
        common = {"Referer": media.ref, "User-Agent": UA, **(info.get("http_headers") or {})}
        audios = [AudioSource(f["url"], {**common, **(f.get("http_headers") or {})})
                  for f in reversed(info.get("formats") or [])
                  if f.get("url", "").startswith("https://") and f.get("vcodec") == "none"
                  and f.get("acodec") not in (None, "none")]
        return tracks, audios, info.get("duration") or media.duration

    def douyin(self, media, force_audio):
        from playwright.sync_api import Error, sync_playwright

        video_id = media.key.split(":")[1]
        player = f"https://open.douyin.com/player/video?vid={video_id}&autoplay=1"
        headers = {"Referer": "https://open.douyin.com/", "User-Agent": UA}

        def is_detail(response):
            url = urlsplit(response.url)
            return (host_is(url.hostname or "", "douyin.com")
                    and "/aweme/detail/" in url.path
                    and parse_qs(url.query).get("aweme_id") == [video_id])

        with sync_playwright() as pw:
            last = None
            for options in ({"channel": "chromium"}, {"channel": "msedge"}, {"channel": "chrome"}):
                try:
                    browser = pw.chromium.launch(headless=True, timeout=8000, **options)
                    try:
                        page = browser.new_page()
                        page.route("**/*", block_player_media)
                        with page.expect_response(is_detail, timeout=13000) as detail:
                            page.goto(player, wait_until="domcontentloaded", timeout=11000)
                        response = detail.value
                        if not response.ok:
                            raise MediaError("restricted" if response.status in (401, 403) else "platform")
                        item = response.json().get("aweme_detail") or {}
                        if str(item.get("aweme_id")) != video_id:
                            raise MediaError("platform")
                        actual_ua = page.evaluate("navigator.userAgent")
                    finally:
                        browser.close()
                    break
                except (Error, ValueError, MediaError) as exc:
                    last = exc
            else:
                raise (last if isinstance(last, MediaError) else MediaError("platform")) from last

        video = item.get("video") or {}
        tracks = []
        if not force_audio:
            for sub in video.get("subtitle_infos") or video.get("subtitleInfos") or []:
                if is_zh(sub.get("language_code") or sub.get("LanguageCodeName")):
                    tracks.append({"url": sub.get("url") or sub.get("Url"),
                                   "ext": sub.get("format") or sub.get("Format") or "srt"})
        headers["User-Agent"] = actual_ua
        rates = sorted(video.get("bit_rate") or [], key=lambda entry: entry.get("bit_rate") or 0)
        addresses = [video.get("audio"), video.get("bit_rate_audio"),
                     *(rate.get("play_addr") for rate in rates), video.get("play_addr"),
                     video.get("play_addr_h264"), video.get("download_addr")]
        audios = [AudioSource(url, headers) for url in dict.fromkeys(find_urls(addresses))
                  if host_is(urlsplit(url).hostname or "", "douyinvod.com", "douyincdn.com", "douyin.com")]
        milliseconds = video.get("duration") or item.get("duration")
        return tracks, audios, milliseconds / 1000 if milliseconds else media.duration

    def subtitle_body(self, track):
        if track.get("data"):
            return track["data"]
        url = track.get("url") or ""
        if not url.startswith("https://"):
            return ""
        with self.session.get(url, timeout=(7, 12), stream=True) as response:
            response.raise_for_status()
            data = response.raw.read(8 * 1024 * 1024 + 1, decode_content=True)
            if len(data) > 8 * 1024 * 1024:
                return ""
            return data.decode("utf-8-sig", errors="replace")


def block_player_media(route):
    request = route.request
    if request.resource_type == "media" or host_is(urlsplit(request.url).hostname or "", "douyinvod.com"):
        route.abort()
    else:
        route.continue_()


def bilibili_error(raw):
    message = raw.lower()
    if any(word in message for word in ("unavailable", "404", "not exist")):
        return MediaError("expired")
    if any(word in message for word in ("403", "login", "412", "subscriber")):
        return MediaError("restricted")
    if "timed out" in message or "connection" in message:
        return MediaError("network")
    return MediaError("platform")


def bilibili_python(media, force_audio):
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import DownloadError

    with YoutubeDL({"quiet": True, "no_warnings": True, "noplaylist": True,
                   "skip_download": True, "writesubtitles": not force_audio,
                   "writeautomaticsub": not force_audio, "cachedir": False,
                   "socket_timeout": 12, "retries": 1, "extractor_retries": 1}) as downloader:
        try:
            return downloader.extract_info(media.ref, download=False)
        except DownloadError as exc:
            raise bilibili_error(str(exc)) from exc


def find_urls(value):
    if isinstance(value, str) and value.startswith("https://"):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from find_urls(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from find_urls(item)
