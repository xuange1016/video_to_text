"""Add redistribution notices and omit Qt add-ons that Pianyu never imports.

This only accepts this checkout's generated .build/release/**/Pianyu directories.
Qt 6.8 module licenses: https://doc.qt.io/qt-6.8/licensing.html
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
QT_ADDONS = (
    "Charts", "DataVisualization", "Graphs", "Quick3D", "QuickTimeline",
    "VirtualKeyboard", "Coap", "Grpc", "HttpServer", "Lottie", "Mqtt", "NetworkAuth",
)
QML_ADDONS = (
    "QtCharts", "QtDataVisualization", "QtGraphs", "QtQuick3D", "QtQuick3DPhysics",
    "QtQuick/Timeline", "QtQuick/VirtualKeyboard", "QtQuick/Studio", "Qt/labs/lottieqt",
    "QtCoap", "QtGrpc", "QtHttpServer", "QtMqtt", "QtNetworkAuth",
)


def prepare(release: Path):
    release = release.resolve(strict=True)
    allowed = (PROJECT / ".build" / "release").resolve(strict=True)
    if not release.is_relative_to(allowed) or release == allowed or release.name != "Pianyu":
        raise ValueError("Expected a generated .build/release/**/Pianyu directory")
    if not (release / "Pianyu.exe").is_file():
        raise ValueError("Pianyu.exe is missing")
    notices = PROJECT / "licenses"
    if not (notices / "manifest.json").is_file():
        raise ValueError("Generate licenses/manifest.json before preparing a release")
    shutil.copy2(PROJECT / "LICENSE", release / "LICENSE")
    shutil.copytree(notices, release / "licenses", dirs_exist_ok=True)
    qt = release / "_internal" / "PySide6"
    candidates = []
    for addon in QT_ADDONS:
        candidates.extend(qt.glob(f"Qt6{addon}*.dll"))
        candidates.extend(qt.glob(f"Qt{addon}*.pyd"))
    candidates.extend(qt / "qml" / name for name in QML_ADDONS)
    candidates.extend((qt / "plugins" / "virtualkeyboard",
                       qt / "plugins" / "platforminputcontexts" / "qtvirtualkeyboardplugin.dll"))
    removed = []
    # Parents before children; every target is rechecked before recursive deletion.
    for target in sorted(set(candidates), key=lambda item: (len(item.parts), str(item))):
        if not target.exists():
            continue
        if not target.resolve().is_relative_to(release) or target.is_symlink():
            raise ValueError(f"Refusing an external or linked runtime target: {target.name}")
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        removed.append(target.relative_to(release).as_posix())
    previous = release / "licenses" / "runtime-preparation.json"
    if previous.is_file():
        removed = sorted(set(removed) | set(json.loads(previous.read_text(encoding="utf-8")).get("removed", [])))
    report = {"excluded_unused_qt_addons": list(QT_ADDONS), "removed": removed,
              "policy": "https://doc.qt.io/qt-6.8/licensing.html",
              "application_code_and_ui": "unchanged"}
    (release / "licenses" / "runtime-preparation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return len(removed)


if __name__ == "__main__":
    removed = prepare(Path(sys.argv[1]))
    print(f"Release notices copied; excluded {removed} unused Qt runtime entries.")
