# 片语 · 视频文案提取

片语是 Windows 11 x64 本地桌面工具，可从抖音、B 站和本地音视频批量提取口播文案。

## 使用

1. 运行安装包，在开始菜单打开「片语」。
2. 在「文案提取」粘贴分享文本、视频链接或本地绝对路径，或选择一个文件夹。点「解析素材」。
3. 勾选需要处理的素材，选保存目录、导出格式和模型。点「提取 N 条文案」。
4. 完成的文稿可在应用内复制，或从结果页打开文件夹。任务记录可以打开旧文稿、重试未完成项和补存。

每条结果存于指定目录下的标题文件夹。TXT 是可读文稿，SRT 带时间码。默认先试可访问的中文字幕；需要语音转写时使用模型并统一转简体。

| 模型 | 场景 | 下载大小 |
| --- | --- | --- |
| 快速 small | 清晰口播、批量初稿 | 约 480 MB |
| 均衡 medium | 一般中文口播，默认 | 约 1.5 GB |

联网素材的解析、下载和首次模型下载需要网络。模型缓存后，本地素材可离线处理。网络视频的媒体由平台提供，访问条件变化时，应用会提示相应失败原因。

任务和文稿存于本机应用数据目录。卸载应用后，已导出的文件仍位于用户选择的保存目录。

## 开发与构建

在 Python 3.11 虚拟环境安装 requirements.txt。用 Playwright 下载 Chromium，把 FFmpeg 和 ffprobe 放在 .build/tools 或加入 PATH，然后运行 main.py。

Windows 安装包由 build.ps1 生成。构建环境还需要 PyInstaller 6.22.3 和 Inno Setup。把 Inno 的 ISCC.exe 放在 .build/inno，把 FFmpeg、ffprobe 和 yt-dlp.exe 及其许可放在 .build/release-tools，将 Playwright 的完整 Chromium 安装到 .build/browsers，并把 Noto Sans SC OTF 与 OFL 放在 .build/fonts。


## 历史版本补录说明

这是片语 1.0.0 的真实里程碑源码，于 2026-11-06 集中整理入库。此处说明反映当时功能，日常使用请下载最新版本。恢复与字节码比对见 [版本历史说明](docs/HISTORY.md)，最新开发说明见 [开发文档](https://github.com/xuange1016/video_to_text/blob/main/docs/DEVELOPMENT.md)。
