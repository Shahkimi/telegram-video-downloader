<#
.SYNOPSIS
  Build the TG Downloader APK on Windows.

.DESCRIPTION
  Runs the tests, checks that no secrets would be packaged, builds `flet build apk`, checks the APK again and prints
  where it is. The first build downloads Flutter, a JDK and the Android SDK parts (several GB, 20-40 minutes).

  One-time setup, from the apk folder:
      py -3.13 -m venv .venv
      .venv\Scripts\pip install -r requirements-dev.txt

  Usage:
      scripts\build_apk.ps1                  # test, build, check
      scripts\build_apk.ps1 -Install         # ...then adb install -r to a connected phone
      scripts\build_apk.ps1 -DnsFix          # networks that block some download hosts (see README)
      scripts\build_apk.ps1 -Arch armeabi-v7a

  Signing: set these user environment variables to sign with your own key (otherwise a debug key is used and a later
  build cannot update the installed app):
      FLET_ANDROID_SIGNING_KEY_STORE  FLET_ANDROID_SIGNING_KEY_STORE_PASSWORD
      FLET_ANDROID_SIGNING_KEY_ALIAS  FLET_ANDROID_SIGNING_KEY_PASSWORD
#>
#Requires -Version 7.0
[CmdletBinding()]
param(
    [switch]$SkipTests,
    [switch]$Install,
    [switch]$DnsFix,
    [switch]$VerboseBuild,
    [string]$Arch = "arm64-v8a",
    [string]$BuildVersion = "",
    [int]$BuildNumber = 0,
    [int]$ProxyPort = 8899
)

$ErrorActionPreference = "Stop"
$ApkDir = Split-Path -Parent $PSScriptRoot
Set-Location $ApkDir

function Step($text) { Write-Host "`n== $text" -ForegroundColor Cyan }

$py = Join-Path $ApkDir ".venv\Scripts\python.exe"
$flet = Join-Path $ApkDir ".venv\Scripts\flet.exe"
if (-not (Test-Path $py) -or -not (Test-Path $flet)) {
    throw "Missing .venv. Run: py -3.13 -m venv .venv ; .venv\Scripts\pip install -r requirements-dev.txt"
}

# ---- things we change temporarily and must put back --------------------------------------------------------------
$flutterSettings = Join-Path $env:USERPROFILE ".flutter_settings"
$hadSettings = Test-Path $flutterSettings
$savedSettings = if ($hadSettings) { Get-Content $flutterSettings -Raw } else { $null }
$proxyProcess = $null

try {
    if (-not $SkipTests) {
        Step "Tests"
        & $py -m pytest -q
        if ($LASTEXITCODE -ne 0) { throw "Tests failed, not building." }
    }

    Step "Secrets check (source)"
    & $py -I scripts\check_no_secrets.py
    if ($LASTEXITCODE -ne 0) { throw "Secrets check failed, not building." }

    Step "Wheels that PyPI only ships as source"
    $wheels = (& $py scripts\prepare_wheels.py | Select-Object -Last 1).Trim()
    if ($LASTEXITCODE -ne 0) { throw "Could not prepare wheels." }
    $env:PIP_FIND_LINKS = $wheels

    if ($DnsFix) {
        Step "Starting DNS-fix proxy on port $ProxyPort"
        $proxyProcess = Start-Process -FilePath $py -ArgumentList @("scripts\dns_fix_proxy.py", "--port", "$ProxyPort") -WindowStyle Hidden -PassThru
        for ($i = 0; $i -lt 30; $i++) {
            try { $c = New-Object Net.Sockets.TcpClient("127.0.0.1", $ProxyPort); $c.Close(); break } catch { Start-Sleep -Milliseconds 200 }
        }
        $env:PIP_PROXY = "http://127.0.0.1:$ProxyPort"
    }

    # Flutter wants to create symlinks for its Windows/Linux desktop targets, which needs Developer Mode or admin.
    # We only build Android, so switch those targets off for the duration of the build when symlinks are not allowed.
    $probe = Join-Path $env:TEMP ("tgdl-symlink-" + [guid]::NewGuid().ToString("N"))
    $symlinksWork = $true
    try {
        New-Item -ItemType SymbolicLink -Path $probe -Target $env:TEMP -ErrorAction Stop | Out-Null
        Remove-Item $probe -Force
    } catch { $symlinksWork = $false }
    if (-not $symlinksWork) {
        Step "No symlink permission: disabling Flutter desktop targets for this build (Developer Mode would avoid this)"
        $settings = if ($hadSettings -and $savedSettings.Trim()) { $savedSettings | ConvertFrom-Json -AsHashtable } else { @{} }
        $settings["enable-windows-desktop"] = $false
        $settings["enable-linux-desktop"] = $false
        $settings | ConvertTo-Json | Set-Content -Path $flutterSettings -Encoding UTF8
    }

    $env:PYTHONUTF8 = "1"
    $env:FLET_CLI_NO_RICH_OUTPUT = "1"

    Step "Signing"
    if ($env:FLET_ANDROID_SIGNING_KEY_STORE) { Write-Host "using keystore $env:FLET_ANDROID_SIGNING_KEY_STORE" }
    else { Write-Host "no FLET_ANDROID_SIGNING_* variables: the APK gets a debug signature" -ForegroundColor Yellow }

    Step "flet build apk"
    $buildArgs = @("build", "apk", "--yes", "--python-version", "3.13")
    if ($VerboseBuild) { $buildArgs += "--verbose" }
    if ($Arch) { $buildArgs += @("--arch", $Arch) }
    if ($BuildVersion) { $buildArgs += @("--build-version", $BuildVersion) }
    if ($BuildNumber -gt 0) { $buildArgs += @("--build-number", "$BuildNumber") }
    & $flet @buildArgs
    if ($LASTEXITCODE -ne 0) { throw "flet build failed (exit $LASTEXITCODE). Re-run with -VerboseBuild for the full log." }

    $apk = Get-ChildItem (Join-Path $ApkDir "build\apk") -Filter *.apk -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $apk) { throw "The build finished but no .apk was found in build\apk." }

    Step "Secrets check (APK)"
    & $py -I scripts\check_no_secrets.py --apk $apk.FullName
    if ($LASTEXITCODE -ne 0) { throw "The APK contains something it must not. Do not distribute it." }

    $hash = (Get-FileHash $apk.FullName -Algorithm SHA256).Hash
    Step "Done"
    Write-Host ("{0}  ({1:N1} MB)" -f $apk.FullName, ($apk.Length / 1MB))
    Write-Host "sha256 $hash"

    if ($Install) {
        $adb = (Get-Command adb -ErrorAction SilentlyContinue).Source
        if (-not $adb) { $adb = Join-Path $env:LOCALAPPDATA "Android\Sdk\platform-tools\adb.exe" }
        if (-not (Test-Path $adb)) { throw "adb not found. Install Android platform-tools or copy the APK to the phone by hand." }
        Step "adb install -r"
        & $adb install -r $apk.FullName
    }
}
finally {
    if ($proxyProcess -and -not $proxyProcess.HasExited) { Stop-Process -Id $proxyProcess.Id -Force }
    if ($hadSettings) { Set-Content -Path $flutterSettings -Value $savedSettings -NoNewline -Encoding UTF8 }
    elseif (Test-Path $flutterSettings) {
        # Flet's own entries (jdk-dir) are rewritten on every build; only our desktop switches need to go.
        try {
            $s = Get-Content $flutterSettings -Raw | ConvertFrom-Json -AsHashtable
            $s.Remove("enable-windows-desktop"); $s.Remove("enable-linux-desktop")
            $s | ConvertTo-Json | Set-Content -Path $flutterSettings -Encoding UTF8
        } catch { }
    }
}
