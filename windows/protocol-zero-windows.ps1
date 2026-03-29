# Protocol Zero — Windows bootstrap
# Run as Administrator in PowerShell
# Installs Ollama, pulls llama3.2:3b, wires Task Scheduler beacon
# Adds Windows Defender exclusions so MS Antimalware doesn't compete

param(
    [string]$TailscaleAuthKey = ""
)

$ErrorActionPreference = "Continue"
$FLAG = "$env:USERPROFILE\.openclutch\.bootstrapped"
$CONF = "$env:USERPROFILE\.openclutch\donate.conf"
$LOG  = "$env:USERPROFILE\.openclutch\bootstrap.log"

function Log($msg) {
    $ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    $line = "[$ts] $msg"
    Write-Host $line
    Add-Content -Path $LOG -Value $line -ErrorAction SilentlyContinue
}

if (Test-Path $FLAG) { Write-Host "Already bootstrapped. Exiting."; exit 0 }

New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.openclutch" | Out-Null

Log "Protocol Zero bootstrap starting (Windows)"

# ── Windows Defender exclusions ────────────────────────────────────────────
# Without this, MsMpEng.exe pins a core during model load and doubles latency
Log "Adding Windows Defender exclusions for Ollama..."
$OllamaPath = "$env:LOCALAPPDATA\Programs\Ollama"
$OllamaModels = "$env:USERPROFILE\.ollama"
try {
    Add-MpPreference -ExclusionPath $OllamaPath -ErrorAction Stop
    Add-MpPreference -ExclusionPath $OllamaModels -ErrorAction Stop
    Add-MpPreference -ExclusionProcess "ollama.exe" -ErrorAction Stop
    Log "Defender exclusions added"
} catch {
    Log "WARNING: Could not add Defender exclusions (need admin). Run as Administrator."
}

# ── Install Tailscale ───────────────────────────────────────────────────────
if (-not (Get-Command tailscale -ErrorAction SilentlyContinue)) {
    Log "Installing Tailscale..."
    $tsUrl = "https://pkgs.tailscale.com/stable/tailscale-setup-latest.exe"
    $tsInstaller = "$env:TEMP\tailscale-setup.exe"
    Invoke-WebRequest -Uri $tsUrl -OutFile $tsInstaller -UseBasicParsing
    Start-Process -FilePath $tsInstaller -ArgumentList "/S" -Wait
    Log "Tailscale installed"
}

if ($TailscaleAuthKey) {
    Log "Joining Tailscale mesh..."
    tailscale up --authkey $TailscaleAuthKey --hostname "neighbor-$env:COMPUTERNAME" 2>&1 | Out-Null
}

$TailscaleIP = (tailscale ip -4 2>$null) -replace '\s',''
if (-not $TailscaleIP) { $TailscaleIP = "0.0.0.0" }
Log "Tailscale IP: $TailscaleIP"

# ── Install Ollama ──────────────────────────────────────────────────────────
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    Log "Installing Ollama..."
    $ollamaUrl = "https://ollama.com/download/OllamaSetup.exe"
    $ollamaInstaller = "$env:TEMP\OllamaSetup.exe"
    Invoke-WebRequest -Uri $ollamaUrl -OutFile $ollamaInstaller -UseBasicParsing
    Start-Process -FilePath $ollamaInstaller -ArgumentList "/S" -Wait
    Start-Sleep -Seconds 5
    Log "Ollama installed"
}

# ── Bind Ollama to Tailscale IP ─────────────────────────────────────────────
Log "Configuring Ollama to bind to $TailscaleIP..."
[System.Environment]::SetEnvironmentVariable("OLLAMA_HOST", "${TailscaleIP}:11434", "Machine")

# Restart Ollama service if running
Get-Process ollama -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
$env:OLLAMA_HOST = "${TailscaleIP}:11434"
Start-Process -FilePath "ollama" -ArgumentList "serve" -WindowStyle Hidden

Start-Sleep -Seconds 5

# ── Detect hardware ─────────────────────────────────────────────────────────
$TotalRAMGB   = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1)
$DonateRAMGB  = [math]::Round($TotalRAMGB * 0.5, 1)
$TotalCores   = (Get-CimInstance Win32_Processor).NumberOfLogicalProcessors
$DonateCores  = [math]::Max(1, [math]::Floor($TotalCores * 0.5))

Log "Hardware: ${TotalRAMGB}GB RAM, ${TotalCores} cores"
Log "Donating: ${DonateRAMGB}GB RAM, ${DonateCores} cores"

# ── Pull model ──────────────────────────────────────────────────────────────
Log "Pulling llama3.2:3b..."
& ollama pull llama3.2:3b
Log "Model ready"

# ── Write donate.conf ────────────────────────────────────────────────────────
@"
enabled=true
cores=$DonateCores
ram_gb=$DonateRAMGB
window_start=22:00
window_stop=04:00
tailscale_ip=$TailscaleIP
model=llama3.2:3b
"@ | Set-Content -Path $CONF -Encoding UTF8

Log "donate.conf written"

# ── Write beacon script ──────────────────────────────────────────────────────
$beaconScript = @'
$conf = Get-Content "$env:USERPROFILE\.openclutch\donate.conf" | ConvertFrom-StringData
$ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$beacon = @{ts=$ts; node=$env:COMPUTERNAME; ip=$conf.tailscale_ip; cores=[int]$conf.cores; ram_gb=[float]$conf.ram_gb; model=$conf.model; status="ONLINE"} | ConvertTo-Json -Compress
Write-Output $beacon
Add-Content -Path "$env:USERPROFILE\.openclutch\beacon.log" -Value $beacon
'@
$beaconScript | Set-Content -Path "$env:USERPROFILE\.openclutch\beacon.ps1" -Encoding UTF8

# ── Wire Task Scheduler ──────────────────────────────────────────────────────
Log "Wiring Task Scheduler..."

# Beacon every 30 minutes
$triggerBeacon = New-ScheduledTaskTrigger -RepetitionInterval (New-TimeSpan -Minutes 30) -Once -At (Get-Date)
$actionBeacon  = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NonInteractive -WindowStyle Hidden -File `"$env:USERPROFILE\.openclutch\beacon.ps1`""
Register-ScheduledTask -TaskName "OpenCLUTCH-Beacon" -Action $actionBeacon -Trigger $triggerBeacon -RunLevel Highest -Force | Out-Null

# 4am stop
$trigger4am = New-ScheduledTaskTrigger -Daily -At "04:00"
$action4am  = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NonInteractive -WindowStyle Hidden -Command `"Get-Process ollama -EA SilentlyContinue | Stop-Process -Force`""
Register-ScheduledTask -TaskName "OpenCLUTCH-Stop" -Action $action4am -Trigger $trigger4am -RunLevel Highest -Force | Out-Null

# 10pm start
$trigger10pm = New-ScheduledTaskTrigger -Daily -At "22:00"
$action10pm  = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NonInteractive -WindowStyle Hidden -Command `"Start-Process ollama -ArgumentList serve -WindowStyle Hidden; Start-Sleep 5; & '$env:USERPROFILE\.openclutch\beacon.ps1'`""
Register-ScheduledTask -TaskName "OpenCLUTCH-Start" -Action $action10pm -Trigger $trigger10pm -RunLevel Highest -Force | Out-Null

Log "Task Scheduler wired"

# ── Fire first beacon ────────────────────────────────────────────────────────
& powershell -NonInteractive -WindowStyle Hidden -File "$env:USERPROFILE\.openclutch\beacon.ps1"
Log "PROTOCOL-ZERO-BEACON FIRED — node online"

New-Item -ItemType File -Force -Path $FLAG | Out-Null
Log "Bootstrap complete. This machine is now a Protocol Zero node."
Write-Host ""
Write-Host "  Node: $env:COMPUTERNAME"
Write-Host "  IP:   $TailscaleIP"
Write-Host "  RAM:  ${DonateRAMGB}GB donated  |  Cores: ${DonateCores}"
Write-Host "  Window: 10pm - 4am"
Write-Host ""
Write-Host "  The mesh grows."
