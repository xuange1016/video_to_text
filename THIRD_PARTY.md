# 第三方组件

发行包的 `licenses/` 包含对应依赖的许可 / 版权正文与 `manifest.json` 来源清单。应用使用动态链接的 Qt 库，保留 `_internal/PySide6` 目录结构，允许按 LGPL 条款替换为兼容的 Qt / PySide6 库。源码和构建方法公开于本仓库；依赖源码位置见该清单。应用没有使用的 Charts、Data Visualization、Graphs、Quick 3D、Quick Timeline、Virtual Keyboard 等 Qt 附加模块在公开安装包中排除。模块许可区分参见 [Qt 6.8 官方说明](https://doc.qt.io/qt-6.8/licensing.html)。

自有源码采用仓库的 MIT License。以下第三方组件保留各自许可，不因应用许可证而改变。

- 文稿分段和 Whisper 转写配置基于 mumuxiaya 的 [video2text](https://github.com/mumuxiaya/video2text)。其 MIT 许可见下文。
- [Qt for Python / PySide6](https://doc.qt.io/qtforpython-6/licenses.html)
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper/blob/master/LICENSE)、[CTranslate2](https://github.com/OpenNMT/CTranslate2/blob/master/LICENSE) 和 [PyAV](https://github.com/PyAV-Org/PyAV/blob/main/LICENSE.txt)
- [OpenCC 的 Python 实现](https://github.com/yichen0831/opencc-python)
- [Playwright](https://github.com/microsoft/playwright-python/blob/main/LICENSE) 及 Chromium，浏览器发行目录带有第三方声明。
- [yt-dlp Windows 二进制发行包](https://github.com/yt-dlp/yt-dlp/releases/tag/2026.10.26)，[第三方许可](https://github.com/yt-dlp/yt-dlp/blob/2026.10.26/THIRD_PARTY_LICENSES.txt)。
- FFmpeg / ffprobe 使用 BtbN 的 Windows LGPL v3 构建 `n8.1.3-32-g760d10603e-20261102`，许可证见工具目录的 `LICENSE.txt`，构建配置与工具 SHA256 见 `licenses/manifest.json`。[对应 FFmpeg 源码](https://github.com/FFmpeg/FFmpeg/tree/760d10603e) · [构建脚本](https://github.com/BtbN/FFmpeg-Builds)。
- Noto Sans SC 字体采用 SIL Open Font License 1.1，随字体保存 `OFL-Noto.txt`；源码位于 [notofonts/noto-cjk](https://github.com/notofonts/noto-cjk)。

## video2text MIT 许可

Copyright (c) 2026 mumuxiaya

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
