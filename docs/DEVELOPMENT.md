# 给想养代码的人

源码仓库只包含片语应用本身。开发环境、模型、媒体工具、浏览器与安装包均不进 Git。

## 环境与源码启动

当前验证环境为 Windows 11 x64、Python 3.11。Linux / macOS / 移动端尚未完成移植与验收。

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe main.py
```

仅运行应用可用 `requirements.txt`。`requirements-dev.txt` 另外安装 pytest 和 PyInstaller，版本与本次发布验证环境一致。

### 媒体工具

准备 `ffmpeg.exe`、`ffprobe.exe`、`yt-dlp.exe`，放到项目的 `.build/tools/`，或加入 `PATH`。FFmpeg / ffprobe 用于读取时长、提取音频和处理视频，独立 yt-dlp.exe 用于平台信息与媒体下载。工具附带许可需一起保留。

下载 Playwright 对应的专用 Chromium：

```powershell
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $PWD '.build/browsers'
.\.venv\Scripts\python.exe -m playwright install chromium
```

启动程序会优先发现 `.build/browsers`。抖音访问使用应用隔离浏览器会话；受限内容仍可能无法获取。

界面使用系统中文字体作为后备。需要与发行版保持相同字体时，将 `NotoSansSC.otf` 和字体许可 `OFL-Noto.txt` 放到 `.build/fonts/`。

### 与日常数据隔离

正常启动通过 Qt 的 `AppLocalDataLocation` 在当前用户目录保存数据库、模型和缓存。调试可指定独立数据目录：

```powershell
$env:PIANYU_DATA_DIR = Join-Path $PWD '.build/dev-data'
.\.venv\Scripts\python.exe main.py
```

同一数据目录通过锁文件限制为一个运行实例。改动只在该终端环境生效。

## 测试

```powershell
New-Item -ItemType Directory -Path '.build' -Force | Out-Null
$env:QT_QPA_PLATFORM = 'offscreen'
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider --basetemp=.build/testtmp
```

`--basetemp` 会由 pytest 清理，请使用专属测试目录。父目录需先存在。

1.4.0 有 36 项测试，覆盖链接与本地路径解析、导出与补存、队列及并发配置、模型下载状态、下载取消进程树、视频发布恢复，以及 QML 点击、拖放和独立下载页交互。多数平台响应和转写在自动化测试中用固定测试数据替代，测试通过不表示所有在线链接可取。

网络全长样本与打包版的人工 / 原生窗口验证范围见 [ACCEPTANCE.md](../ACCEPTANCE.md)。干净 Windows 虚拟机的完整安装验收仍待补充。

## 源码地图

| 文件 | 负责什么 |
| --- | --- |
| `main.py` | 桌面入口、QML 控制器、后台任务调度、设置、进度和页面状态 |
| `ui/Main.qml` | 工作台、下载页、队列、历史、设置与动效 |
| `ui/PaperSprite.qml` / `ui/assets/` | 纸精灵、品牌图标与视觉资产 |
| `platforms.py` | 分享文本解析、去重、平台元信息、字幕与媒体地址 |
| `engine.py` | 模型下载 / 缓存、转写、简体转换、时间轴与文稿导出 |
| `downloads.py` | 可取消的下载、视频校验、完整缓存与 MP4 安全发布 |
| `storage.py` | SQLite 设置 / 批次 / 逐项状态，以及提取与下载任务流程 |
| `deployment/make_icon.py` | 从自绘 SVG 生成多尺寸 Windows 图标 |
| `build.ps1` / `packaging.iss` | PyInstaller 目录构建与 Inno Setup 安装包 |

并发采用后台线程池，设置限制 1–3 条任务；Whisper 底层推理由 CTranslate2 执行，同型号模型在本批应用进程内复用。当前实现没有独立进程分发协议，移动端也没有已交付的客户端。

## 构建安装包

构建工具使用 PyInstaller 6.22.3 和 Inno Setup。版本从根目录 `VERSION` 读取。首先准备以下目录：

```text
.build/
├─ release-tools/          # ffmpeg.exe、ffprobe.exe、yt-dlp.exe 及许可
├─ browsers/               # Playwright 下载的完整 Chromium 与配套目录
├─ fonts/                  # NotoSansSC.otf、OFL-Noto.txt
└─ inno/
   └─ ISCC.exe             # Inno Setup，须保留其安装目录的配套文件
```

开始构建：

```powershell
.\build.ps1 -PortableOnly  # 仅生成可运行的完整目录
.\build.ps1                # 生成目录并编译安装包
```

仓库包含发布时采集的 `licenses/`。更新依赖或运行时后，先运行 `.\.venv\Scripts\python.exe deployment/collect_licenses.py` 更新对应声明和清单，再构建。`deployment/prepare_release.py` 会复制声明并去除未使用的 GPL-only Qt 附加模块，具体模块依据 [Qt 6.8 许可列表](https://doc.qt.io/qt-6.8/licensing.html)。构建脚本与 Inno Setup 均不会把模型打包进安装程序。

产物为 `.build/release/版本号/Pianyu/` 与 `dist/Pianyu-版本号-win-x64-Setup.exe`。复制便携目录时需保留整个文件夹。`-PackageOnly` 复用已暂存的本版本可执行文件，但仍会同步当前 UI 与文档，因此不能用它重新封装历史版本。

历史安装包通过 Inno Setup 直接读取冻结的历史目录重新封装；不会把当前 QML 覆盖到旧版本上。版本与来源说明见 [HISTORY.md](HISTORY.md)。

发布到 GitHub 时，源码、测试和小型界面图进入 Git，安装包及其 `SHA256SUMS.txt` 放入对应 Release。模型和第三方运行时留在本地构建目录。

## 许可证与贡献

仓库沿用初始提交中的 MIT License；所使用的第三方代码与发行包组件按各自许可保留声明，参见 [THIRD_PARTY.md](../THIRD_PARTY.md)。

欢迎提 Issue 或 PR。描述问题时请附片语版本、复现步骤、错误提示与可公开的样本，日志中的私人路径请先脱敏。涉及平台获取链路的改动，应同时检查取消、失败后的队列继续和待保存恢复行为。
