$ErrorActionPreference = "Stop"

if (Test-Path .env) {
  Write-Host "Loading .env"
} else {
  if (Test-Path .env.example) {
    Copy-Item .env.example .env -ErrorAction SilentlyContinue
  } else {
    Write-Host "No .env found. Create one with API_ID, API_HASH, MONITORED_GROUPS, etc."
  }
}

py -m app