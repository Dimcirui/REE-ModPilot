# Sign every executable produced by `pnpm tauri build`:
#   - The outer modpilot.exe
#   - The bundled modpilot-backend.exe sidecar (inside resources/)
#   - The MSI + NSIS installers
#
# Run AFTER `pnpm tauri build`. Tauri's own signing pipeline (via
# certificateThumbprint in tauri.conf.json) only signs the outer exe and
# the installers — it does NOT sign the sidecar. SmartScreen flags any
# unsigned exe spawned from a signed parent, so we sign the sidecar too.
#
# Usage:
#   $env:TAURI_SIGNING_CERT_THUMBPRINT = "<sha1 thumbprint>"
#   .\sign_bundle.ps1                              # signs everything
#   .\sign_bundle.ps1 -TimestampUrl "..."          # add RFC 3161 timestamp
#
# Cert must already be installed in Cert:\CurrentUser\My or LocalMachine\My.
# Generate a dev cert via `.\generate_dev_cert.ps1`.

param(
    [string]$Thumbprint = $env:TAURI_SIGNING_CERT_THUMBPRINT,
    [string]$TimestampUrl = "http://timestamp.sectigo.com"
)

$ErrorActionPreference = 'Stop'

if (-not $Thumbprint) {
    Write-Error "Provide -Thumbprint or set TAURI_SIGNING_CERT_THUMBPRINT env var."
    exit 1
}

# Locate signtool.exe — ships with Windows SDK; common path is under
# Program Files (x86)\Windows Kits\10\bin\<sdk-version>\x64\.
$signtool = $null
$candidates = Get-ChildItem -Recurse -ErrorAction SilentlyContinue `
    -Path "${env:ProgramFiles(x86)}\Windows Kits\10\bin" `
    -Filter signtool.exe |
    Where-Object { $_.DirectoryName -match 'x64' } |
    Sort-Object FullName -Descending
if ($candidates) { $signtool = $candidates[0].FullName }
if (-not $signtool) {
    Write-Error "signtool.exe not found. Install Windows SDK or set %PATH%."
    exit 1
}
Write-Host "Using signtool: $signtool" -ForegroundColor DarkGray

$root = Split-Path -Parent $PSScriptRoot       # src-tauri/
$bundleDir = Join-Path $root "target\release\bundle"
$mainExe = Join-Path $root "target\release\modpilot.exe"
$sidecarExe = Join-Path $root "target\release\resources\binaries\backend\modpilot-backend.exe"

$targets = @()
if (Test-Path $mainExe) { $targets += $mainExe }
if (Test-Path $sidecarExe) {
    $targets += $sidecarExe
} else {
    # Unbundled / --no-bundle build keeps the sidecar in binaries/backend/
    $altSidecar = Join-Path $root "binaries\backend\modpilot-backend.exe"
    if (Test-Path $altSidecar) { $targets += $altSidecar }
}
if (Test-Path "$bundleDir\msi") {
    $targets += (Get-ChildItem "$bundleDir\msi" -Filter '*.msi').FullName
}
if (Test-Path "$bundleDir\nsis") {
    $targets += (Get-ChildItem "$bundleDir\nsis" -Filter '*-setup.exe').FullName
}

if ($targets.Count -eq 0) {
    Write-Error "No build outputs found. Run `pnpm tauri build` first."
    exit 1
}

foreach ($t in $targets) {
    Write-Host "Signing $t ..." -ForegroundColor Cyan
    & $signtool sign /sha1 $Thumbprint /fd sha256 /tr $TimestampUrl /td sha256 $t
    if ($LASTEXITCODE -ne 0) {
        Write-Error "signtool failed on $t"
        exit $LASTEXITCODE
    }
}

Write-Host ""
Write-Host "Signed $($targets.Count) files." -ForegroundColor Green
Write-Host "Verify with: signtool verify /pa /v <file>" -ForegroundColor DarkGray
