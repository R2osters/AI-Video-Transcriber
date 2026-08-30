# Telecharge ffmpeg (build essentials de gyan.dev, source referencee par ffmpeg.org)
# et place ffmpeg.exe + ffprobe.exe dans desktop/resources/bin (embarques dans l'installeur).
# NOTE: script ASCII pur (PowerShell 5.1 lit les .ps1 sans BOM en ANSI).
$ErrorActionPreference = "Stop"
$Desktop = Split-Path $PSScriptRoot -Parent
$BinDir = Join-Path $Desktop "resources\bin"
$Url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"

if ((Test-Path (Join-Path $BinDir "ffmpeg.exe")) -and (Test-Path (Join-Path $BinDir "ffprobe.exe"))) {
    Write-Host ">> ffmpeg deja present dans resources\bin - rien a faire."
    exit 0
}

New-Item -ItemType Directory -Force $BinDir | Out-Null
$Tmp = Join-Path $env:TEMP "avt-ffmpeg"
New-Item -ItemType Directory -Force $Tmp | Out-Null
$Zip = Join-Path $Tmp "ffmpeg.zip"

Write-Host ">> Telechargement $Url ..."
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
Invoke-WebRequest -Uri $Url -OutFile $Zip

Write-Host ">> Extraction..."
Expand-Archive -Path $Zip -DestinationPath $Tmp -Force

$Exe = Get-ChildItem -Path $Tmp -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1
$Probe = Get-ChildItem -Path $Tmp -Recurse -Filter "ffprobe.exe" | Select-Object -First 1
if (-not $Exe) { throw "ffmpeg.exe introuvable dans l archive" }
if (-not $Probe) { throw "ffprobe.exe introuvable dans l archive" }

Copy-Item $Exe.FullName (Join-Path $BinDir "ffmpeg.exe") -Force
Copy-Item $Probe.FullName (Join-Path $BinDir "ffprobe.exe") -Force
Remove-Item -Recurse -Force $Tmp

Write-Host ">> OK : resources\bin\ffmpeg.exe + ffprobe.exe"
