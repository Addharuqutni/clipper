# =============================================================================
# ClipperAI — stop semua layanan (implementasi PowerShell).
#
# Dipanggil oleh stop-all.cmd. Ditulis sebagai file terpisah supaya logika
# tidak perlu di-escape berlapis lewat `cmd ^`.
#
# Strategi:
#   1. Proses python/node/cmd yang command line-nya memuat folder repo ini.
#   2. Pemilik port 8000/3000 — juga hanya bila milik repo ini.
#   3. Tunggu sampai port benar-benar lepas; laporkan bila masih tertahan
#      (mis. dipakai aplikasi lain, yang sengaja tidak dihentikan).
#
# Keluar dengan 0 bila kedua port bebas, 1 bila masih ada yang tertahan.
# =============================================================================

$ErrorActionPreference = 'SilentlyContinue'

$ports = @(8000, 3000)
$self  = $PID
# Akar repo (scripts\..). Hanya proses yang command line-nya memuat folder ini
# yang dihentikan: pola nama saja (uvicorn app.main, node next, anak
# multiprocessing) juga cocok dengan proyek lain di komputer yang sama.
$root  = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

function Get-TargetProcesses {
    Get-CimInstance Win32_Process | Where-Object {
        $_.ProcessId -ne $self -and $_.CommandLine -and
        $_.CommandLine -like "*$root*" -and (
            $_.Name -in @('python.exe', 'node.exe') -or
            ($_.Name -eq 'cmd.exe' -and $_.CommandLine -match 'run-(api|web)\.cmd')
        )
    }
}

function Get-PortOwners {
    # Pemilik port 8000/3000 hanya dihentikan bila memang proses repo ini.
    $owners = @()
    foreach ($port in $ports) {
        Get-NetTCPConnection -LocalPort $port -State Listen |
            ForEach-Object { $owners += $_.OwningProcess }
    }
    $owners | Where-Object { $_ -and $_ -ne $self } | Sort-Object -Unique | Where-Object {
        $p = Get-CimInstance Win32_Process -Filter "ProcessId = $_"
        $p -and $p.CommandLine -like "*$root*"
    }
}

$targets = @(Get-TargetProcesses)
$owners  = @(Get-PortOwners)
$all     = @($targets.ProcessId) + $owners | Sort-Object -Unique

if ($all.Count -eq 0) {
    Write-Host '   (tidak ada layanan ClipperAI yang berjalan)'
} else {
    foreach ($procId in $all) {
        $p = Get-CimInstance Win32_Process -Filter "ProcessId = $procId"
        $name = if ($p) { $p.Name } else { 'proses sudah berhenti' }
        Write-Host "   - menghentikan PID $procId ($name)"
        Stop-Process -Id $procId -Force
    }
}

# Tunggu port benar-benar lepas (proses butuh waktu melepas socket).
for ($i = 0; $i -lt 20; $i++) {
    $remaining = @(Get-PortOwners)
    if ($remaining.Count -eq 0) { break }
    Start-Sleep -Milliseconds 300
}

$still = @(Get-PortOwners)
if ($still.Count -gt 0) {
    Write-Host ''
    foreach ($port in $ports) {
        $c = Get-NetTCPConnection -LocalPort $port -State Listen
        if ($c) { Write-Host "   [!] port $port masih dipakai PID $($c.OwningProcess)" }
    }
    exit 1
}

Write-Host '   semua port bebas.'
exit 0
