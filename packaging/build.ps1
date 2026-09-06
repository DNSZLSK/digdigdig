<#
.SYNOPSIS
    Build le .exe Windows de DDD (fenetre native) avec PyInstaller.

.DESCRIPTION
    Sort dist\DDD\DDD.exe (+ dossier de support). Double-clic = la fenetre s'ouvre.
    Pour Mac/Linux : meme principe avec PyInstaller sur la plateforme cible, ou
    `flet build macos|linux` (voir packaging/README.md).

.EXAMPLE
    .\packaging\build.ps1
#>
[CmdletBinding()]
param([switch]$Clean)

$ErrorActionPreference = 'Stop'
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "venv introuvable : $py" }

Write-Host "Verification core / CLI / GUI avant packaging..."
& $py -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "Tests en echec : packaging annule." }

if ($Clean) {
    Write-Host "Nettoyage build/ et dist/..."
    Remove-Item -Recurse -Force "$Root\build", "$Root\dist" -ErrorAction SilentlyContinue
}

Write-Host "PyInstaller : build de DDD.exe (peut prendre 1-3 min)..."
& $py -m PyInstaller "packaging\ddd.spec" --noconfirm --distpath "$Root\dist" --workpath "$Root\build"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller en echec." }

$exe = Join-Path $Root "dist\DDD\DDD.exe"
if (Test-Path $exe) {
    $size = [math]::Round((Get-ChildItem "$Root\dist\DDD" -Recurse -File | Measure-Object Length -Sum).Sum / 1MB, 1)
    Write-Host ""
    Write-Host "OK : $exe"
    Write-Host "Taille totale du bundle : $size MB"
    Write-Host "Distribue le dossier dist\DDD\ entier (zippe-le)."
} else {
    throw "Build echoue : $exe absent"
}
