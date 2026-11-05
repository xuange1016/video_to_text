# 第三方许可与来源记录

这里保存片语桌面发行所使用的组件许可、版权和第三方声明原文。片语自身的许可证见安装目录或仓库根目录的 `LICENSE`；各个组件按各自的许可证分发，这些正文不改变片语自身的许可证。

`manifest.json` 记录当前构建环境中每个已采集发行包的名称、确切版本、许可元数据、正文来源与 SHA-256。记录范围包含 `requirements.txt` 的运行依赖和 PyInstaller 启动器所需的构建依赖。某个发行包出现在清单中，并不代表它的所有可选功能都被打包，也不代表它出现在每一个历史 EXE 中。

- `python/`：从实际安装的 wheel 复制的许可与声明，保留正文和原始相对路径。wheel 未提供正文的 CTranslate2、tokenizers、flatbuffers 从对应版本的官方源码标签补齐。
- `python-runtime-*/`：实际构建所用 Python 运行时的许可。
- `qt-*/`：与 PySide6 版本相同的官方 LGPL、GPL 和相关例外原文。wheel 自带的商业许可引用仍原样保留在 `python/` 下，保留引用不表示片语取得了商业授权。未使用的 Qt 扩展由发行准备步骤排除，具体结果见安装包中的 `runtime-preparation.json`。
- `yt-dlp-*/`：与随包可执行文件版本相同的官方许可及第三方组件声明。
- `ffmpeg-*/`：通过实际 FFmpeg、FFprobe 的版本输出确认的源码提交、完整构建配置、相应上游许可正文与供应方提供的许可。不同许可文件按上游原样保留，实际启用组件以构建配置为准。
- `chromium-*/`：由实际随包浏览器的 `chrome://credits/` 导出的所有许可块，包括页面默认折叠的正文。`build.json` 记录浏览器版本、Playwright 修订号及源码入口。
- `font-noto-sans-sc/`：随字体提供的 OFL 正文。

历史版本在首次 GitHub 发布时补齐了这份分发材料。补充声明及排除应用未使用的 Qt 扩展不会修改历史应用源码、EXE 或 QML；每个安装包的具体准备记录随包提供。这里是正文和来源清单，不冒充完整的二进制依赖审计报告。

在准备好与构建一致的工具、浏览器和字体后，可重新采集或检查记录。

```powershell
.\.venv\Scripts\python.exe -B .\deployment\collect_licenses.py
.\.venv\Scripts\python.exe -B .\deployment\collect_licenses.py --check
```

`--check` 只校验已保存的相对路径、正文大小和 SHA-256，不联网，也不启动浏览器。默认采集会复用已保存的固定版本上游正文，使用 `--refresh` 可重新获取并再次导出浏览器声明。依赖或工具版本变化后，应重新采集并审查清单，再创建安装包。
