param([switch]$PackageOnly, [switch]$PortableOnly, [string]$StandaloneDir)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$root = $PSScriptRoot
$env:PYINSTALLER_CONFIG_DIR = Join-Path $root '.build/pyinstaller-cache'
$env:PYTHONUSERBASE = Join-Path $root '.build/python-user'
$version = '1.1.0'
$release = Join-Path $root ".build/release/$version/Pianyu"

if (-not $PackageOnly) {
    if (-not $StandaloneDir) {
        & '.\.venv\Scripts\python.exe' -B '.\deployment\make_icon.py' '.\ui\assets\pianyu.svg' '.\.build\pianyu.ico'
        if ($LASTEXITCODE) { throw 'Could not render the Windows icon.' }
        $arguments = @(
            '--onedir', '--windowed', '--noconfirm', '--name', 'Pianyu',
            '--icon', (Join-Path $root '.build/pianyu.ico'),
            '--distpath', '.build/pyi', '--workpath', '.build/pyi-work', '--specpath', '.build',
            '--exclude-module', 'yt_dlp',
            '--hidden-import', 'PySide6.QtQuick', '--hidden-import', 'PySide6.QtQuickControls2', '--hidden-import', 'PySide6.QtSvg',
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
Copy-Item -Path 'ui/*' -Destination "$release/ui" -Recurse -Force
Copy-Item -Path '.build/fonts/*' -Destination "$release/ui" -Force
if ($PortableOnly) { Write-Output "$release/Pianyu.exe"; return }
& '.\.build\inno\ISCC.exe' "/DAppVersion=$version" '.\packaging.iss'
if ($LASTEXITCODE) { throw 'Inno Setup compilation failed.' }
Get-FileHash -LiteralPath "$root/dist/Pianyu-$version-win-x64-Setup.exe" -Algorithm SHA256
