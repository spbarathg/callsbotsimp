$ErrorActionPreference = "Stop"

if (Test-Path .env) {
  Write-Host "Loading .env"
} else {
  Copy-Item .env.example .env -ErrorAction SilentlyContinue
}

py -m app