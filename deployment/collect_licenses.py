"""Collect the installed runtime's notices without copying personal paths.

Run with the same Python environment that creates the desktop bundle. Existing
upstream files are reused unless --refresh is given; --check only verifies the
committed inventory and SHA-256 values and does not need network access.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as metadata
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


ROOT = Path(__file__).resolve().parents[1]
LEGAL_NAME = re.compile(r"^(?:licen[sc]e(?:ref)?|copying|copyright|notice|third.?party.?notices|authors)(?:[._-]|$)", re.I)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


class Collector:
    def __init__(self, output, refresh=False):
        self.output = output
        self.refresh = refresh
        self.files = {}
        self.distributions = []
        self.components = []
        self.output.mkdir(parents=True, exist_ok=True)

    def put(self, relative, data, source):
        path = self.output / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        self.files[relative] = {"path": relative, "sha256": sha256(data), "bytes": len(data), "source": source}
        return relative

    def upstream(self, relative, url):
        path = self.output / relative
        if path.is_file() and not self.refresh:
            data = path.read_bytes()
        else:
            request = urllib.request.Request(url, headers={"User-Agent": "Pianyu-license-collector/1"})
            for attempt in range(3):
                try:
                    with urllib.request.urlopen(request, timeout=20) as response:
                        data = response.read(8 * 1024 * 1024 + 1)
                    break
                except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                    if isinstance(exc, urllib.error.HTTPError) and exc.code < 500 or attempt == 2:
                        raise RuntimeError(f"Could not fetch pinned upstream notice: {relative}") from exc
            if not data or len(data) > 8 * 1024 * 1024 or data.lstrip().lower().startswith(b"<!doctype html"):
                raise ValueError(f"The upstream notice was not a bounded text file: {relative}")
        return self.put(relative, data, url)

    def installed(self):
        names = [Requirement(line).name for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
                 if line.strip() and not line.lstrip().startswith("#")]
        pending = [(name, "runtime") for name in names] + [("pyinstaller", "build-bootloader")]
        seen = set()
        while pending:
            name, scope = pending.pop(0)
            dist = metadata.distribution(name)
            key = canonicalize_name(dist.metadata["Name"])
            if key in seen:
                continue
            seen.add(key)
            docs = []
            for file in dist.files or []:
                relative = PurePosixPath(str(file).replace("\\", "/"))
                if not LEGAL_NAME.match(relative.name) or relative.suffix.lower() in {".py", ".pyc", ".pyd", ".so"}:
                    continue
                if relative.is_absolute() or ".." in relative.parts:
                    continue
                source = Path(dist.locate_file(file))
                if source.is_file():
                    target = f"python/{key}-{dist.version}/{relative.as_posix()}"
                    docs.append(self.put(target, source.read_bytes(),
                                         f"installed-wheel:{dist.metadata['Name']}=={dist.version}/{relative.as_posix()}"))
            self.distributions.append({
                "name": dist.metadata["Name"], "version": dist.version, "scope": scope,
                "license_metadata": dist.metadata.get("License-Expression") or dist.metadata.get("License"),
                "project_urls": dist.metadata.get_all("Project-URL") or [], "files": sorted(docs),
            })
            for value in dist.requires or []:
                requirement = Requirement(value)
                if not requirement.marker or requirement.marker.evaluate({"extra": ""}):
                    pending.append((requirement.name, scope))

        missing = {item["name"]: item["version"] for item in self.distributions if not item["files"]}
        fixed = {
            "ctranslate2": ("OpenNMT/CTranslate2", "LICENSE"),
            "tokenizers": ("huggingface/tokenizers", "LICENSE"),
            "flatbuffers": ("google/flatbuffers", "LICENSE"),
        }
        for item in self.distributions:
            key = canonicalize_name(item["name"])
            if not item["files"] and key in fixed:
                repo, filename = fixed[key]
                version = item["version"]
                url = f"https://raw.githubusercontent.com/{repo}/v{version}/{filename}"
                item["files"].append(self.upstream(f"python/{key}-{version}/{filename}", url))
                missing.pop(item["name"], None)
        if missing:
            raise ValueError("No license text was collected for: " + ", ".join(f"{n}=={v}" for n, v in missing.items()))

    def fixed_notices(self):
        qt = metadata.version("PySide6")
        ytdlp = metadata.version("yt-dlp")
        urls = []
        for filename in ("LGPL-3.0-only.txt", "GPL-2.0-only.txt", "GPL-3.0-only.txt", "Qt-GPL-exception-1.0.txt",
                         "GFDL-1.3-no-invariants-only.txt"):
            urls.append((f"qt-{qt}/{filename}",
                         f"https://raw.githubusercontent.com/pyside/pyside-setup/v{qt}/LICENSES/{filename}"))
        urls.extend([
            (f"yt-dlp-{ytdlp}/LICENSE", f"https://raw.githubusercontent.com/yt-dlp/yt-dlp/{ytdlp}/LICENSE"),
            (f"yt-dlp-{ytdlp}/THIRD_PARTY_LICENSES.txt",
             f"https://raw.githubusercontent.com/yt-dlp/yt-dlp/{ytdlp}/THIRD_PARTY_LICENSES.txt"),
        ])
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(lambda pair: self.upstream(*pair), urls))

        python = Path(sys.base_prefix) / "LICENSE.txt"
        if not python.is_file():
            raise ValueError("The Python runtime's LICENSE.txt could not be located")
        self.put(f"python-runtime-{sys.version.split()[0]}/LICENSE.txt", python.read_bytes(), "installed-python:LICENSE.txt")
        self.components.append({"name": "Python", "version": sys.version.split()[0],
                                "source": f"https://github.com/python/cpython/tree/v{sys.version.split()[0]}"})

    def assets(self, tools, browsers, fonts):
        for name in ("ffmpeg", "ffprobe"):
            executable = tools / (name + ".exe")
            if not executable.is_file():
                raise ValueError(f"The staged {name}.exe is required to identify its exact build")
            completed = subprocess.run([str(executable), "-version"], capture_output=True, check=True,
                                       creationflags=NO_WINDOW, encoding="utf-8", errors="replace")
            text = completed.stdout
            match = re.search(r"-g([0-9a-f]{7,40})", text.splitlines()[0])
            if not match:
                raise ValueError(f"The staged {name} build did not report a source revision")
            revision = match.group(1)
            relative = f"ffmpeg-{revision}/{name}-build.txt"
            self.put(relative, text.encode("utf-8"), f"staged-tool:{name}.exe -version")
            self.components.append({"name": name, "version": text.splitlines()[0],
                                    "sha256": file_sha256(executable),
                                    "source": f"https://github.com/FFmpeg/FFmpeg/tree/{revision}",
                                    "build_output": relative,
                                    "supplier": "https://github.com/BtbN/FFmpeg-Builds"})
        for filename in ("LICENSE.md", "COPYING.LGPLv2.1", "COPYING.LGPLv3", "COPYING.GPLv2", "COPYING.GPLv3", "CREDITS"):
            self.upstream(f"ffmpeg-{revision}/{filename}",
                          f"https://raw.githubusercontent.com/FFmpeg/FFmpeg/{revision}/{filename}")
        license_file = tools / "LICENSE.txt"
        self.put(f"ffmpeg-{revision}/SUPPLIER-LICENSE.txt", license_file.read_bytes(), "staged-tool:tools/LICENSE.txt")
        ytdlp = tools / "yt-dlp.exe"
        if not ytdlp.is_file():
            raise ValueError("The staged yt-dlp.exe is required")
        version = subprocess.run([str(ytdlp), "--version"], capture_output=True, check=True,
                                 creationflags=NO_WINDOW, encoding="utf-8", errors="replace").stdout.strip()
        if version != metadata.version("yt-dlp"):
            raise ValueError("The staged yt-dlp.exe and installed yt-dlp metadata have different versions")
        self.components.append({"name": "yt-dlp executable", "version": version,
                                "sha256": file_sha256(ytdlp),
                                "source": f"https://github.com/yt-dlp/yt-dlp/releases/tag/{version}"})

        notice = fonts / "OFL-Noto.txt"
        self.put("font-noto-sans-sc/OFL-Noto.txt", notice.read_bytes(), "staged-font:OFL-Noto.txt")
        self.components.append({"name": "Noto Sans SC OTF", "sha256": file_sha256(fonts / "NotoSansSC.otf"),
                                "source": "https://github.com/notofonts/noto-cjk", "license": "OFL-1.1"})
        self.chromium(browsers)

    def chromium(self, browsers):
        from playwright.sync_api import sync_playwright

        candidates = sorted(browsers.glob("chromium-*/chrome-win/chrome.exe"))
        if len(candidates) != 1:
            raise ValueError("Expected exactly one staged Chromium chrome-win/chrome.exe")
        executable = candidates[0]
        revision = executable.parents[1].name
        relative = f"{revision}/THIRD-PARTY-NOTICES.txt"
        info_relative = f"{revision}/build.json"
        existing = self.output / info_relative
        browser_hash = file_sha256(executable.parent / "chrome.dll")
        info = json.loads(existing.read_text(encoding="utf-8")) if existing.is_file() else {}
        if self.refresh or not (self.output / relative).is_file() or info.get("chrome_dll_sha256") != browser_hash:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=str(executable), headless=True,
                                                    args=["--enable-logging=stderr", "--log-level=3"])
                try:
                    page = browser.new_page()
                    page.goto("chrome://credits/", wait_until="load", timeout=30000)
                    count = page.locator(".license").count()
                    text = page.locator("body").text_content()
                    if count < 100 or not text or len(text) < 500000:
                        raise ValueError("The bundled Chromium credits page did not expose its complete notices")
                    info = {"name": "Chromium", "version": browser.version, "playwright_revision": revision,
                            "chrome_dll_sha256": browser_hash,
                            "embedded_license_blocks": count, "notice_source": "bundled-browser:chrome://credits/",
                            "source": f"https://chromium.googlesource.com/chromium/src/+/{browser.version}"}
                    self.put(relative, text.encode("utf-8"), "bundled-browser:chrome://credits/ (all hidden license blocks included)")
                finally:
                    browser.close()
            self.put(info_relative, json_bytes(info), "bundled-browser:version and embedded credits")
        if relative not in self.files:
            self.put(relative, (self.output / relative).read_bytes(), "bundled-browser:chrome://credits/ (all hidden license blocks included)")
            self.put(info_relative, json_bytes(info), "bundled-browser:version and embedded credits")
        self.components.append(info)

    def finish(self):
        manifest = {
            "schema_version": 1,
            "description": "License texts copied without alteration from installed wheels, pinned upstream revisions and the bundled browser. This is a notices inventory, not a claim that every installed distribution is present in every historical executable.",
            "roots": ["requirements.txt runtime dependencies", "PyInstaller bootloader/build dependencies"],
            "distributions": sorted(self.distributions, key=lambda item: canonicalize_name(item["name"])),
            "components": sorted(self.components, key=lambda item: item["name"].lower()),
            "files": sorted(self.files.values(), key=lambda item: item["path"]),
        }
        (self.output / "manifest.json").write_bytes(json_bytes(manifest))
        print(f"Collected {len(self.files)} notice files for {len(self.distributions)} distributions and {len(self.components)} components.")


def check(output):
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    for item in manifest["files"]:
        path = (output / item["path"]).resolve()
        if not path.is_relative_to(output.resolve()) or not path.is_file():
            raise ValueError(f"Missing or invalid notice path: {item['path']}")
        data = path.read_bytes()
        if len(data) != item["bytes"] or sha256(data) != item["sha256"]:
            raise ValueError(f"Notice checksum changed: {item['path']}")
    if any(not item["files"] for item in manifest["distributions"]):
        raise ValueError("A distribution has no collected license text")
    print(f"Verified {len(manifest['files'])} notice files and their SHA-256 values.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify the committed inventory without network access")
    parser.add_argument("--refresh", action="store_true", help="Refetch pinned upstream notices and regenerate browser credits")
    parser.add_argument("--output", type=Path, default=ROOT / "licenses")
    parser.add_argument("--tools", type=Path, default=ROOT / ".build" / "release-tools")
    parser.add_argument("--browsers", type=Path, default=ROOT / ".build" / "browsers")
    parser.add_argument("--fonts", type=Path, default=ROOT / ".build" / "fonts")
    options = parser.parse_args()
    if options.check:
        check(options.output)
        return
    collector = Collector(options.output, options.refresh)
    collector.installed()
    collector.fixed_notices()
    collector.assets(options.tools, options.browsers, options.fonts)
    collector.finish()
    check(options.output)


if __name__ == "__main__":
    main()
