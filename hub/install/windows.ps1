# TouchStream Hub for Windows 10 and 11
#
#   irm https://raw.githubusercontent.com/Cinderwire-co/TouchStream/main/hub/install/windows.ps1 | iex
#
# Installs the hub (with uv, which brings its own Python), MediaMTX and FFmpeg (their
# official builds, checksum-verified); starts the hub now and at every sign-in, in the
# background; lets spokes through Windows Firewall (asks for permission once); adds
# "TouchStream Hub" to the Start menu; and opens the dashboard. Run it again to update:
# settings and recordings stay.
#
#   $env:TOUCHSTREAM_UNINSTALL = 1; irm ... | iex   remove the hub (keeps settings and recordings)
#   $env:TOUCHSTREAM_UNINSTALL = 'everything'        and its settings, YouTube sign-ins and recordings (to the Recycle Bin)
#   $env:TOUCHSTREAM_SOURCE                          install the hub from a folder or URL instead of GitHub
#   $env:TOUCHSTREAM_PORT                            dashboard port (default: 8080, or the next free one)
#   $env:TOUCHSTREAM_NO_OPEN                         don't open the dashboard at the end (the installer app sets it)
#
# Copyright (c) 2025 Will Reeves and TouchStream Contributors
# Licensed under the MIT License - see LICENSE file for details

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'     # downloads are many times faster without the progress bar

$Branch = if ($env:TOUCHSTREAM_BRANCH) { $env:TOUCHSTREAM_BRANCH } else { 'main' }
$Source = if ($env:TOUCHSTREAM_SOURCE) { $env:TOUCHSTREAM_SOURCE } else {
    "https://github.com/Cinderwire-co/TouchStream/archive/refs/heads/$Branch.zip#subdirectory=hub" }
$MediaMtxVersion = '1.21.3'
$Task = 'TouchStream Hub'
$Data = Join-Path $env:LOCALAPPDATA 'TouchStream Hub'      # the hub's own data folder (settings.py)
$Bin = Join-Path $Data 'bin'
$Mtx = Join-Path $Bin 'mediamtx.exe'
$Shortcut = Join-Path ([Environment]::GetFolderPath('Programs')) 'TouchStream Hub.url'
$Rules = 'TouchStream Hub:*'      # its firewall rules' names

function Say($message) { Write-Host "==> $message" -ForegroundColor Yellow }
function Quote($text) { "'" + $text.Replace("'", "''") + "'" }

function Test-Free($port, $protocol = 'TCP') {
    # By trying it: Windows' own port lists can leave ports out (over SSH, for one)
    try {
        if ($protocol -eq 'TCP') { $socket = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Any, $port); $socket.Start(); $socket.Stop() }
        else { $socket = [Net.Sockets.UdpClient]::new($port); $socket.Close() }
        return $true
    } catch { return $false }
}

function Get-Text($url) {
    # Through a file: Windows PowerShell 5.1 hands back some downloads as bytes, not text
    $file = Join-Path $env:TEMP "touchstream-$([IO.Path]::GetRandomFileName())"
    Invoke-WebRequest -UseBasicParsing $url -OutFile $file
    $text = Get-Content $file -Raw
    Remove-Item $file -Force
    return $text
}

function Get-Verified($url, $file, $sha256) {
    Invoke-WebRequest -UseBasicParsing $url -OutFile $file
    if ((Get-FileHash $file -Algorithm SHA256).Hash -ne $sha256.ToUpper()) {
        Remove-Item $file -Force
        throw "The download of $url didn't match its checksum"
    }
}

function Stop-Hub {
    if (Get-ScheduledTask -TaskName $Task -ErrorAction SilentlyContinue) { Stop-ScheduledTask -TaskName $Task -ErrorAction SilentlyContinue }
    # The hub and its MediaMTX, so their files can be replaced and their ports freed
    Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe' OR Name = 'python.exe'" |
        Where-Object { $_.CommandLine -like '*touchstream_hub*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
    Get-Process mediamtx -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $Mtx } | Stop-Process -Force
}

function Invoke-Elevated($script) {
    # Directly when already administrator, else one UAC prompt; returns $false if declined
    $admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
    if ($admin) { Invoke-Expression $script; return $true }
    try {
        Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden -ArgumentList '-NoProfile', '-Command', $script
        return $true
    } catch { return $false }
}

# A task registered from an elevated session (PowerShell run as administrator, or over SSH, where an administrator's
# session is elevated) can't be replaced or removed from a normal one: Windows says access denied (0x80070005).
# Remove it as administrator: one UAC prompt.
function Remove-HubTask($name) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
    if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
        Say "Removing the hub's old sign-in task (Windows asks for permission)"
        Invoke-Elevated "Unregister-ScheduledTask -TaskName '$name' -Confirm:`$false" | Out-Null
    }
    -not (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue)
}

# Our own uv, not whichever one another app put on the PATH
$Uv = "$HOME\.local\bin\uv.exe"

# ---- Uninstall ----
if ($env:TOUCHSTREAM_UNINSTALL) {
    Say 'Stopping the hub'
    Stop-Hub
    Say 'Removing the hub'
    Remove-HubTask $Task | Out-Null
    Remove-HubTask "$Task Update" | Out-Null
    Remove-Item $Shortcut -Force -ErrorAction SilentlyContinue
    if (Test-Path $Uv) {
        # uv reports on stderr, which Windows PowerShell would treat as a failure here
        $ErrorActionPreference = 'Continue'; & $Uv tool uninstall touchstream-hub 2>&1 | Out-Null; $ErrorActionPreference = 'Stop'
    }
    if (Get-NetFirewallRule -DisplayName $Rules -ErrorAction SilentlyContinue) {
        Say 'Removing the firewall rules (Windows asks for permission)'
        Invoke-Elevated "Remove-NetFirewallRule -DisplayName '$Rules'" | Out-Null
    }
    if ($env:TOUCHSTREAM_UNINSTALL -eq 'everything') {
        # Its settings and sign-ins, and the default recordings folder, to the Recycle Bin (a recordings
        # folder chosen in the dashboard stays: it may hold other things)
        Say 'Moving its settings and recordings to the Recycle Bin'
        Add-Type -AssemblyName Microsoft.VisualBasic
        foreach ($folder in @($Data, (Join-Path ([Environment]::GetFolderPath('MyVideos')) 'TouchStream'))) {
            if (Test-Path $folder) {
                [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory($folder, 'OnlyErrorDialogs', 'SendToRecycleBin')
            }
        }
        Say 'TouchStream Hub removed, with its settings and recordings: they''re in the Recycle Bin until you empty it.'
        return
    }
    Say "TouchStream Hub removed. Your settings are still in $Data and your recordings where you chose."
    return
}

# ---- Dashboard port: the one from before, else 8080 or the next free one ----
Stop-Hub
$Port = $env:TOUCHSTREAM_PORT
$existing = Get-ScheduledTask -TaskName $Task -ErrorAction SilentlyContinue
if (-not $Port -and $existing -and $existing.Actions[0].Arguments -match '--port (\d+)') { $Port = $Matches[1] }
if (-not $Port) {
    $Port = 8080..8099 | Where-Object { Test-Free $_ } | Select-Object -First 1
    if (-not $Port) { throw 'No free port between 8080 and 8099; set $env:TOUCHSTREAM_PORT' }
}
$Busy = @(@{ Port = 8890; Protocol = 'UDP'; For = 'video from spokes' }, @{ Port = 1935; Protocol = 'TCP'; For = 'video from encoders' }) |
    Where-Object { -not (Test-Free $_.Port $_.Protocol) }

# ---- uv (installs the hub and its own Python, nothing system-wide) ----
if (-not (Test-Path $Uv)) {
    Say 'Installing uv'
    $env:UV_NO_MODIFY_PATH = '1'
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression *> $null
}
New-Item -ItemType Directory -Force $Bin | Out-Null

# ---- FFmpeg: the gyan.dev build (the one FFmpeg's site links), in the hub's own folder. Not one already
# on the PATH: apps like ImageMagick ship a cut-down FFmpeg without the ffprobe the hub needs ----
if (-not ((Test-Path "$Bin\ffmpeg.exe") -and (Test-Path "$Bin\ffprobe.exe"))) {
    Say 'Installing FFmpeg'
    $zip = Join-Path $env:TEMP 'ffmpeg-essentials.zip'
    $base = 'https://www.gyan.dev/ffmpeg/builds'
    Get-Verified "$base/ffmpeg-release-essentials.zip" $zip (Get-Text "$base/ffmpeg-release-essentials.zip.sha256").Trim()
    $unpacked = Join-Path $env:TEMP 'ffmpeg-essentials'
    Expand-Archive $zip $unpacked -Force
    Get-ChildItem $unpacked -Recurse -Include ffmpeg.exe, ffprobe.exe | Copy-Item -Destination $Bin -Force
    Remove-Item $zip, $unpacked -Recurse -Force
}

# ---- MediaMTX: the official release, where the hub looks for it ----
if (-not ((Test-Path $Mtx) -and ((& $Mtx --version) -eq "v$MediaMtxVersion"))) {
    Say "Installing MediaMTX $MediaMtxVersion"
    $file = "mediamtx_v${MediaMtxVersion}_windows_amd64.zip"
    $base = "https://github.com/bluenviron/mediamtx/releases/download/v$MediaMtxVersion"
    $line = (Get-Text "$base/checksums.sha256") -split "`n" | Where-Object { $_ -like "* *$file" }
    $zip = Join-Path $env:TEMP $file
    Get-Verified "$base/$file" $zip ($line -split ' ')[0]
    $unpacked = Join-Path $env:TEMP 'mediamtx'
    Expand-Archive $zip $unpacked -Force
    Copy-Item (Join-Path $unpacked 'mediamtx.exe') $Mtx -Force
    Remove-Item $zip, $unpacked -Recurse -Force
}

# ---- The hub ----
Say 'Installing TouchStream Hub'
if (Test-Path $Source) { $Source = ([Uri](Resolve-Path $Source).Path).AbsoluteUri }
& $Uv tool install --quiet --force --python 3.12 "touchstream-hub[transcribe] @ $Source"
if ($LASTEXITCODE) { throw "Couldn't install the hub (uv exited with $LASTEXITCODE)" }
$Tool = Join-Path (& $Uv tool dir) 'touchstream-hub'
$Pythonw = Join-Path $Tool 'Scripts\pythonw.exe'
Copy-Item (Join-Path $Tool 'Lib\site-packages\touchstream_hub\static\img\touchstream.ico') $Data -Force

# ---- Let spokes reach it ----
# Rules for the hub's own programs on their own ports: then Windows doesn't pop up asking about
# them (where Cancel would block them). From anywhere on Private and Domain networks; on networks
# Windows calls Public (often a home or church network it was never told about) only from the
# same local network. The hub's Python is uv's, behind a link: the rules name both paths.
$python = Split-Path (& (Join-Path $Tool 'Scripts\python.exe') -c 'import sys; print(sys._base_executable)')
$pythons = @($python) + @((Get-Item $python).Target | Where-Object { $_ }) | ForEach-Object { Join-Path $_ 'pythonw.exe' }
$wanted = @(foreach ($program in $pythons) {
    @{ Name = 'TouchStream Hub: dashboard and spokes'; Program = $program; Protocol = 'TCP'; Port = $Port }
    @{ Name = 'TouchStream Hub: finding spokes'; Program = $program; Protocol = 'UDP'; Port = 9999 } }) + @(
    @{ Name = 'TouchStream Hub: video from spokes (SRT)'; Program = $Mtx; Protocol = 'UDP'; Port = 8890 }
    @{ Name = 'TouchStream Hub: video from encoders (RTMP)'; Program = $Mtx; Protocol = 'TCP'; Port = 1935 })
$want = $wanted | ForEach-Object { "$($_.Name)|$($_.Program)|$($_.Protocol)|$($_.Port)".ToLower() } | Sort-Object -Unique
$have = Get-NetFirewallRule -DisplayName $Rules -ErrorAction SilentlyContinue | Where-Object { $_.Profile -ne 'Public' } |
    ForEach-Object { $ports = $_ | Get-NetFirewallPortFilter
        "$($_.DisplayName)|$(($_ | Get-NetFirewallApplicationFilter).Program)|$($ports.Protocol)|$($ports.LocalPort)".ToLower() } |
    Sort-Object -Unique
if (Compare-Object @($want) @($have)) {
    Say 'Letting spokes through Windows Firewall (Windows asks for permission)'
    $script = (@("Remove-NetFirewallRule -DisplayName '$Rules' -ErrorAction SilentlyContinue") + @($wanted | ForEach-Object {
        $rule = "-DisplayName $(Quote $_.Name) -Program $(Quote $_.Program) -Direction Inbound -Action Allow " +
            "-Protocol $($_.Protocol) -LocalPort $($_.Port)"
        "New-NetFirewallRule $rule -Profile Private,Domain | Out-Null"
        "New-NetFirewallRule $rule -Profile Public -RemoteAddress LocalSubnet | Out-Null" })) -join '; '
    if (-not (Invoke-Elevated $script)) {
        Write-Host '  Skipped: spokes on other computers may not reach the hub until you allow it in Windows Firewall.'
    }
}

# ---- Start now and at every sign-in, in the background (pythonw: no console window) ----
Say "Starting the hub on port $Port"
$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name   # (USERDOMAIN is wrong over SSH)
$action = New-ScheduledTaskAction -Execute $Pythonw -Argument "-m touchstream_hub --port $Port" -WorkingDirectory $Data
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$options = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$register = {
    Register-ScheduledTask -TaskName $Task -Action $action -Trigger $trigger -Settings $options -Principal $principal `
        -Description 'TouchStream Hub: schedules and records your spokes, and streams them to YouTube' -Force | Out-Null
}
try { & $register } catch {
    # an old task this session may not replace (see Remove-HubTask): remove it, then register ours
    if ($_.FullyQualifiedErrorId -notmatch '0x80070005' -and $_.Exception.HResult -ne -2147024891) { throw }
    if (-not (Remove-HubTask $Task)) { throw "Windows didn't allow replacing the hub's old sign-in task. Run the installer again and choose Yes when Windows asks." }
    & $register
}
Start-ScheduledTask -TaskName $Task

# ---- "TouchStream Hub" in the Start menu: opens the dashboard ----
Set-Content $Shortcut "[InternetShortcut]`r`nURL=http://localhost:$Port/`r`nIconFile=$Data\touchstream.ico`r`nIconIndex=0`r`n"

# ---- Done ----
Say 'Waiting for the hub'
$up = $false
foreach ($i in 1..60) {
    # 127.0.0.1, not localhost: Windows tries IPv6 first and takes 2 s to be refused there
    try { Invoke-RestMethod "http://127.0.0.1:$Port/api/state" -TimeoutSec 3 | Out-Null; $up = $true; break } catch { Start-Sleep 1 }
}
if (-not $up) { throw "The hub didn't start; see $Data\hub.log" }
$ip = (Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway } | Select-Object -First 1).IPv4Address.IPAddress
Write-Host ''
Write-Host '  TouchStream Hub is running.'
Write-Host ''
Write-Host "    Dashboard:           http://localhost:$Port   (or ""TouchStream Hub"" in the Start menu)"
Write-Host "    Address for spokes:  $(if ($ip) { $ip } else { "this PC's IP address" }):$Port"
Write-Host ''
foreach ($port in $Busy) {
    Write-Host "  Note: another program on this PC is using $($port.Protocol) port $($port.Port), which the hub needs for $($port.For)."
}
if (-not $env:TOUCHSTREAM_NO_OPEN) {   # (the installer app carries on itself)
    try { Start-Process "http://localhost:$Port/" } catch { }   # (no desktop when run over SSH)
}
