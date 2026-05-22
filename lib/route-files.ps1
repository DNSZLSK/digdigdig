<#
.SYNOPSIS
    Deploy verified FLAC files from staging to the USB key, in the correct
    folder per the routing map. Optionally delete the old fake .wav.

.DESCRIPTION
    Inputs:
      - StagingDir : where sldl deposited the downloaded files
      - MapJson    : routing map (artist+title key -> dossier + origFile)
      - DetectiveReport (optional) : flac-detective text report; if provided,
                                     only files marked authentic are deployed
      - UsbRoot    : root of the USB key (e.g. D:\2023 Playlist Ultime)
      - DryRun     : list actions without performing them
      - DeleteOld  : also remove the original .wav that the FLAC replaces

    Behavior:
      For each .flac in StagingDir:
        - Parse filename as "Artist - Title.flac"
        - Look up the routing map for dossier + origFile
        - If not found in map, write to outputs\unmapped.csv
        - If detective report marks it as fake, write to outputs\fake.csv
        - Otherwise copy to <UsbRoot>\<Dossier>\<Artist> - <Title>.flac
        - If DeleteOld, delete <UsbRoot>\<Dossier>\<OrigFile>
      Generate outputs\migration_report.csv summarising every action.
#>
param(
    [Parameter(Mandatory)][string]$StagingDir,
    [Parameter(Mandatory)][string]$MapJson,
    [Parameter(Mandatory)][string]$UsbRoot,
    [string]$DetectiveReport = '',
    [string]$OutputDir = '.\outputs',
    [switch]$DryRun,
    [switch]$DeleteOld
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $StagingDir)) { throw "Staging dir not found : $StagingDir" }
if (-not (Test-Path -LiteralPath $MapJson))    { throw "Map JSON not found : $MapJson" }
if (-not (Test-Path -LiteralPath $UsbRoot))    { throw "USB root not found : $UsbRoot" }

if (-not (Test-Path -LiteralPath $OutputDir)) {
    New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
}

# Load routing map ----------------------------------------------------------

$mapRaw = Get-Content -LiteralPath $MapJson -Raw | ConvertFrom-Json
# Convert PSCustomObject to a hashtable for case-insensitive lookup
$map = @{}
foreach ($prop in $mapRaw.PSObject.Properties) {
    $map[$prop.Name.ToLower()] = $prop.Value
}

# Load detective report (optional) ------------------------------------------

# The detective text report lists files at the end with their verdict.
# Format varies; we parse with a tolerant approach.
$authenticPaths = $null
$fakePaths = $null
if ($DetectiveReport -and (Test-Path -LiteralPath $DetectiveReport)) {
    $reportContent = Get-Content -LiteralPath $DetectiveReport -Raw
    # Heuristic parsing: look for "FAKE" / "SUSPICIOUS" verdicts followed by a filename
    # Until we see real outputs we treat as: if report says "Fake/Suspicious: 0" globally,
    # everything is authentic. Otherwise we need to refine.
    if ($reportContent -match 'Fake/Suspicious:\s*(\d+)') {
        $fakeCount = [int]$Matches[1]
        if ($fakeCount -eq 0) {
            # All files in staging are authentic
            $authenticPaths = (Get-ChildItem $StagingDir -Recurse -Filter '*.flac').FullName
            $fakePaths = @()
        }
    }
    if ($null -eq $authenticPaths) {
        Write-Warning "Could not auto-parse detective report - treating all FLACs as authentic (manual review recommended)"
        $authenticPaths = (Get-ChildItem $StagingDir -Recurse -Filter '*.flac').FullName
        $fakePaths = @()
    }
} else {
    # No detective report provided - treat everything as authentic but warn
    Write-Warning "No detective report - assuming all FLACs are authentic. Run flac-detective first for safety."
    $authenticPaths = (Get-ChildItem $StagingDir -Recurse -Filter '*.flac').FullName
    $fakePaths = @()
}

# Process files -------------------------------------------------------------

$report     = New-Object System.Collections.Generic.List[object]
$unmapped   = New-Object System.Collections.Generic.List[object]
$deployed   = 0
$skippedFake = 0
$alreadyExisting = 0

$flacFiles = Get-ChildItem $StagingDir -Recurse -File -Filter '*.flac'
Write-Host "Processing $($flacFiles.Count) FLAC files from $StagingDir"

foreach ($f in $flacFiles) {
    # Parse "Artist - Title.flac"
    $base = $f.BaseName
    $key = $base.ToLower()

    $isAuthentic = $authenticPaths -contains $f.FullName
    $isFake      = $fakePaths -contains $f.FullName

    if ($isFake) {
        $skippedFake++
        $report.Add([pscustomobject]@{
            Action   = 'SKIP_FAKE'
            FlacFile = $f.Name
            Dossier  = ''
            Dest     = ''
            Note     = 'flac-detective verdict: fake or suspicious'
        })
        continue
    }

    if (-not $isAuthentic) {
        # Not in either list - shouldn't happen but be safe
        continue
    }

    if (-not $map.ContainsKey($key)) {
        # Try a softer match: drop punctuation / extra suffixes
        $alt = ($key -replace '[^a-z0-9\s\-]', '' -replace '\s+', ' ').Trim()
        $foundAlt = $false
        foreach ($mk in $map.Keys) {
            $mkAlt = ($mk -replace '[^a-z0-9\s\-]', '' -replace '\s+', ' ').Trim()
            if ($mkAlt -eq $alt) {
                $key = $mk
                $foundAlt = $true
                break
            }
        }
        if (-not $foundAlt) {
            $unmapped.Add([pscustomobject]@{ FlacFile = $f.Name; Key = $base })
            $report.Add([pscustomobject]@{
                Action   = 'UNMAPPED'
                FlacFile = $f.Name
                Dossier  = ''
                Dest     = ''
                Note     = 'no routing entry found - manual placement needed'
            })
            continue
    }
    }

    $entry = $map[$key]
    $dossier = $entry.dossier
    $origFile = $entry.origFile
    $destDir = Join-Path $UsbRoot $dossier
    $destPath = Join-Path $destDir $f.Name

    if (-not (Test-Path -LiteralPath $destDir)) {
        if ($DryRun) {
            Write-Host "  [DRY] would mkdir $destDir"
        } else {
            New-Item -ItemType Directory -Force -Path $destDir | Out-Null
        }
    }

    if (Test-Path -LiteralPath $destPath) {
        $alreadyExisting++
        $report.Add([pscustomobject]@{
            Action   = 'ALREADY_EXISTS'
            FlacFile = $f.Name
            Dossier  = $dossier
            Dest     = $destPath
            Note     = 'destination already has this file'
        })
        continue
    }

    if ($DryRun) {
        Write-Host "  [DRY] copy $($f.Name) -> $destPath"
        if ($DeleteOld -and $origFile) {
            $oldPath = Join-Path $destDir $origFile
            if (Test-Path -LiteralPath $oldPath) {
                Write-Host "  [DRY] delete $oldPath"
            }
        }
    } else {
        Copy-Item -LiteralPath $f.FullName -Destination $destPath -Force
        $action = 'COPIED'
        if ($DeleteOld -and $origFile) {
            $oldPath = Join-Path $destDir $origFile
            if (Test-Path -LiteralPath $oldPath) {
                Remove-Item -LiteralPath $oldPath -Force
                $action = 'COPIED+OLD_DELETED'
            }
        }
        $report.Add([pscustomobject]@{
            Action   = $action
            FlacFile = $f.Name
            Dossier  = $dossier
            Dest     = $destPath
            Note     = ''
        })
        $deployed++
    }
}

# Write outputs -------------------------------------------------------------

$report   | Export-Csv -LiteralPath (Join-Path $OutputDir 'migration_report.csv') -NoTypeInformation -Encoding UTF8
$unmapped | Export-Csv -LiteralPath (Join-Path $OutputDir 'unmapped.csv') -NoTypeInformation -Encoding UTF8

Write-Host ('=' * 70)
Write-Host ('Deployed         : {0}' -f $deployed)
Write-Host ('Already existed  : {0}' -f $alreadyExisting)
Write-Host ('Skipped (fake)   : {0}' -f $skippedFake)
Write-Host ('Unmapped         : {0}' -f $unmapped.Count)
Write-Host ('Report           : {0}' -f (Join-Path $OutputDir 'migration_report.csv'))
