param([switch]$PackageOnly, [string]$StandaloneDir)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$root = $PSScriptRoot
$env:PYINSTALLER_CONFIG_DIR = Join-Path $root '.build/pyinstaller-cache'
$env:PYTHONUSERBASE = Join-Path $root '.build/python-user'
$release = Join-Path $root '.build/release/Pianyu'

if (-not $PackageOnly) {
    if (-not $StandaloneDir) {
        $arguments = @(
            '--onedir', '--windowed', '--noconfirm', '--name', 'Pianyu',
            '--distpath', '.build/pyi', '--workpath', '.build/pyi-work', '--specpath', '.build',
            '--exclude-module', 'yt_dlp',
            '--hidden-import', 'PySide6.QtQuick', '--hidden-import', 'PySide6.QtQuickControls2',
            '--hidden-import', 'huggingface_hub._snapshot_download', '--hidden-import', 'huggingface_hub.file_download',
            '--collect-data', 'opencc', '--collect-data', 'faster_whisper', '--collect-all', 'playwright',
            'main.py'
        )
        & '.\.venv\Scripts\python.exe' -m PyInstaller @arguments
        if ($LASTEXITCODE) { throw 'PyInstaller standalone build failed.' }
        $StandaloneDir = '.build/pyi/Pianyu'
    }
    $standalone = (Resolve-Path -LiteralPath $StandaloneDir).Path
    if (Test-Path -LiteralPath $release) {
        $previous = Get-Item -LiteralPath $release -Force
        if ($previous.FullName -ne [IO.Path]::GetFullPath($release) -or
            ($previous.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw 'The generated release directory did not resolve to its expected location.'
        }
        Remove-Item -LiteralPath $previous.FullName -Recurse -Force
    }
    New-Item -ItemType Directory -Path $release -Force | Out-Null
    Copy-Item -Path "$standalone/*" -Destination $release -Recurse -Force
    New-Item -ItemType Directory -Path "$release/ui","$release/tools","$release/browsers" -Force | Out-Null
    Copy-Item -Path '.build/release-tools/*' -Destination "$release/tools" -Force
    Copy-Item -Path '.build/browsers/chromium-*','.build/browsers/ffmpeg-*','.build/browsers/winldd-*' -Destination "$release/browsers" -Recurse -Force
    Copy-Item -LiteralPath 'README.md','THIRD_PARTY.md' -Destination $release -Force
}

if (-not (Test-Path -LiteralPath "$release/Pianyu.exe")) { throw 'Pianyu.exe is missing from the staged release.' }
Copy-Item -LiteralPath 'ui/Main.qml' -Destination "$release/ui" -Force
Copy-Item -Path '.build/fonts/*' -Destination "$release/ui" -Force
& '.\.build\inno\ISCC.exe' '.\packaging.iss'
if ($LASTEXITCODE) { throw 'Inno Setup compilation failed.' }
Get-FileHash -LiteralPath "$root/dist/Pianyu-1.0.0-win-x64-Setup.exe" -Algorithm SHA256
