# Generates a self-signed code-signing cert for *local* / *internal* testing
# of the signing pipeline. Does NOT bypass Windows SmartScreen — only a
# CA-issued cert does that (see README "Code signing" section for the EV
# cert upgrade path).
#
# Usage:
#   .\generate_dev_cert.ps1                    # generates + installs cert
#   .\generate_dev_cert.ps1 -Thumbprint        # prints thumbprint only
#
# Outputs:
#   - Self-signed cert in Cert:\CurrentUser\My
#   - Auto-installs the public cert in Cert:\CurrentUser\Root so signed
#     bundles trust on this machine. Distribute the .cer to other dev
#     machines via `Import-Certificate -CertStoreLocation Cert:\LocalMachine\Root`.
#   - Prints the SHA1 thumbprint — pass this to `pnpm tauri build` via
#     the TAURI_SIGNING_CERT_THUMBPRINT env var, or paste into
#     tauri.conf.json's `bundle.windows.certificateThumbprint`.

param(
    [switch]$Thumbprint
)

$ErrorActionPreference = 'Stop'

$subject = "CN=ModPilot Dev Code Signing"
$existing = Get-ChildItem Cert:\CurrentUser\My | Where-Object { $_.Subject -eq $subject } | Select-Object -First 1

if (-not $existing) {
    if ($Thumbprint) {
        Write-Host "No existing dev cert found. Run without -Thumbprint to create one." -ForegroundColor Yellow
        exit 1
    }
    Write-Host "Creating self-signed code-signing cert..." -ForegroundColor Cyan
    $cert = New-SelfSignedCertificate `
        -Subject $subject `
        -CertStoreLocation Cert:\CurrentUser\My `
        -Type CodeSigningCert `
        -KeyUsage DigitalSignature `
        -KeyAlgorithm RSA `
        -KeyLength 2048 `
        -HashAlgorithm SHA256 `
        -NotAfter (Get-Date).AddYears(3)

    # Install public cert in Trusted Root so signed bundles validate on this
    # machine. (Note: requires admin or per-user store. CurrentUser\Root is
    # per-user and needs no elevation.)
    $exported = Export-Certificate -Cert $cert -FilePath "$env:TEMP\modpilot-dev-cert.cer"
    Import-Certificate -FilePath $exported.FullName -CertStoreLocation Cert:\CurrentUser\Root | Out-Null
    Write-Host "  installed in Cert:\CurrentUser\My + Cert:\CurrentUser\Root" -ForegroundColor Green
    $existing = $cert
} elseif (-not $Thumbprint) {
    Write-Host "Dev cert already exists." -ForegroundColor Yellow
}

if ($Thumbprint) {
    Write-Output $existing.Thumbprint
} else {
    Write-Host ""
    Write-Host "Thumbprint: $($existing.Thumbprint)" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "To sign a bundle, set the env var before pnpm tauri build:" -ForegroundColor White
    Write-Host "  `$env:TAURI_SIGNING_CERT_THUMBPRINT = '$($existing.Thumbprint)'" -ForegroundColor Gray
    Write-Host "  pnpm tauri build" -ForegroundColor Gray
    Write-Host ""
    Write-Host "Or paste the thumbprint into tauri.conf.json:" -ForegroundColor White
    Write-Host '  "bundle": { "windows": { "certificateThumbprint": "...", "digestAlgorithm": "sha256" } }' -ForegroundColor Gray
}
