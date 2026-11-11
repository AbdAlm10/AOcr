# Start AOcr on Windows. Git Bash can run ./run.sh instead.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $MyInvocation.MyCommand.Path)

if (Test-Path .env) {
  Get-Content .env | ForEach-Object {
    if ($_ -match '^\s*([^#][^=]+)=(.*)$') {
      Set-Item -Path "Env:$($Matches[1].Trim())" -Value $Matches[2].Trim()
    }
  }
}

if (-not $env:WEB_PORT) { $env:WEB_PORT = "8787" }
if (-not $env:PORT) { $env:PORT = "8791" }
$env:API_INTERNAL_URL = "http://127.0.0.1:$($env:PORT)"

if (-not (Test-Path .venv)) { python -m venv .venv }
& .\.venv\Scripts\python -m pip install -q -U pip
& .\.venv\Scripts\python -m pip install -q -e ./server
if (-not (Test-Path web\node_modules)) { npm install --prefix web }

Write-Host ""
Write-Host "  On this PC:    http://127.0.0.1:$($env:WEB_PORT)"
Write-Host "  On your phone: http://<LAN-ip-from-ipconfig>:$($env:WEB_PORT)"
Write-Host "  Same Wi-Fi. Allow the port in Windows Firewall if the phone cannot connect."
Write-Host ""

$api = Start-Process -FilePath .\.venv\Scripts\python -ArgumentList "-m","waraq" -PassThru -NoNewWindow
try {
  Set-Location web
  if ($env:CHANDRA_DEV -eq "1") {
    npx next dev --turbopack --hostname 0.0.0.0 --port $env:WEB_PORT
  } else {
    npm run build
    npx next start --hostname 0.0.0.0 --port $env:WEB_PORT
  }
} finally {
  if ($api -and -not $api.HasExited) { Stop-Process -Id $api.Id -Force }
}
