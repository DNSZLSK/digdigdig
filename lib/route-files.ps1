<#
.SYNOPSIS
    Deploy verified FLAC files from staging to the USB key, in the correct
    folder per the routing map. Optionally delete the old fake .wav.

.DESCRIPTION
    Source of truth = sldl's own _index.csv (in staging\sldl_input\) which maps
    each downloaded file to the artist/title from our input CSV. No more
    filename-parsing guesswork.

    Inputs:
      - StagingDir       : where sldl deposited the downloaded files
      - MapJson          : routing map (artist+title key -> dossier + origFile)
      - DetectiveReport  : optional flac-detective text report (when count of fakes
                           is 0, all files are deployed; otherwise we emit
                           a warning and skip suspect files - real per-file
                           parsing TBD)
      - UsbRoot          : root of the USB key (e.g. D:\2023 Playlist Ultime)
      - DryRun           : list actions without performing them
      - DeleteOld        : also remove the original .wav that the FLAC replaces
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

# --- load sldl index (source of truth for file <-> input mapping) ----------

$indexPath = Join-Path $StagingDir 'sldl_input\_index.csv'
if (-not (Test-Path -LiteralPath $indexPath)) {
    throw "sldl _index.csv not found : $indexPath. Run sldl first."
}
$indexRows = Import-Csv -LiteralPath $indexPath -Encoding UTF8

# --- load routing map (artist+title key -> dossier + origFile) -------------

$mapRaw = Get-Content -LiteralPath $MapJson -Raw | ConvertFrom-Json
$map = @{}
foreach ($prop in $mapRaw.PSObject.Properties) {
    $map[$prop.Name.ToLower()] = $prop.Value
}

# --- detective report (optional, simple all-good or per-file later) --------

$fakeFiles = @{}   # set of full paths flagged as fake
if ($DetectiveReport -and (Test-Path -LiteralPath $DetectiveReport)) {
    $reportContent = Get-Content -LiteralPath $DetectiveReport -Raw
    if ($reportContent -match 'Fake/Suspicious:\s*(\d+)') {
        $fakeCount = [int]$Matches[1]
        if ($fakeCount -gt 0) {
            Write-Warning "Detective report says $fakeCount fake files - per-file parsing not yet implemented, all FLACs treated as authentic"
        }
    }
}

# --- process index rows ----------------------------------------------------

$report      = New-Object System.Collections.Generic.List[object]
$unmapped    = New-Object System.Collections.Generic.List[object]
$deployed    = 0
$alreadyExisting = 0
$failedDl    = 0
$skippedFake = 0
$missingFile = 0

foreach ($row in $indexRows) {
    $filepath = $row.filepath
    $artist = $row.artist
    $title = $row.title

    # If filepath is empty, sldl didn't manage to DL this row
    if (-not $filepath) {
        $report.Add([pscustomobject]@{
            Action   = 'NOT_DOWNLOADED'
            Artist   = $artist
            Title    = $title
            FlacFile = ''
            Dossier  = ''
            Dest     = ''
            Note     = "sldl state=$($row.state) reason=$($row.failurereason)"
        })
        $failedDl++
        continue
    }

    if (-not (Test-Path -LiteralPath $filepath)) {
        $report.Add([pscustomobject]@{
            Action   = 'FILE_MISSING'
            Artist   = $artist
            Title    = $title
            FlacFile = (Split-Path $filepath -Leaf)
            Dossier  = ''
            Dest     = ''
            Note     = "indexed but file not on disk : $filepath"
        })
        $missingFile++
        continue
    }

    if ($fakeFiles.ContainsKey($filepath)) {
        $report.Add([pscustomobject]@{
            Action   = 'SKIP_FAKE'
            Artist   = $artist
            Title    = $title
            FlacFile = (Split-Path $filepath -Leaf)
            Dossier  = ''
            Dest     = ''
            Note     = 'detective: fake/suspicious'
        })
        $skippedFake++
        continue
    }

    $key = ($artist + ' - ' + $title).ToLower()
    if (-not $map.ContainsKey($key)) {
        $unmapped.Add([pscustomobject]@{ Artist = $artist; Title = $title; FlacFile = (Split-Path $filepath -Leaf) })
        $report.Add([pscustomobject]@{
            Action   = 'UNMAPPED'
            Artist   = $artist
            Title    = $title
            FlacFile = (Split-Path $filepath -Leaf)
            Dossier  = ''
            Dest     = ''
            Note     = 'key not in routing map'
        })
        continue
    }

    $entry = $map[$key]
    $dossier = $entry.dossier
    $origFile = $entry.origFile
    $destDir = Join-Path $UsbRoot $dossier
    $destPath = Join-Path $destDir (Split-Path $filepath -Leaf)

    if (-not (Test-Path -LiteralPath $destDir)) {
        if ($DryRun) {
            Write-Host "  [DRY] mkdir $destDir"
        } else {
            New-Item -ItemType Directory -Force -Path $destDir | Out-Null
        }
    }

    if (Test-Path -LiteralPath $destPath) {
        $report.Add([pscustomobject]@{
            Action   = 'ALREADY_EXISTS'
            Artist   = $artist
            Title    = $title
            FlacFile = (Split-Path $filepath -Leaf)
            Dossier  = $dossier
            Dest     = $destPath
            Note     = 'destination already has this file'
        })
        $alreadyExisting++
        continue
    }

    if ($DryRun) {
        Write-Host ("  [DRY] copy {0} -> {1}" -f (Split-Path $filepath -Leaf), $destPath)
        if ($DeleteOld -and $origFile) {
            $oldPath = Join-Path $destDir $origFile
            if (Test-Path -LiteralPath $oldPath) {
                Write-Host ("  [DRY] delete {0}" -f $oldPath)
            }
        }
        $report.Add([pscustomobject]@{
            Action   = 'DRY_COPY'
            Artist   = $artist
            Title    = $title
            FlacFile = (Split-Path $filepath -Leaf)
            Dossier  = $dossier
            Dest     = $destPath
            Note     = ''
        })
    } else {
        Copy-Item -LiteralPath $filepath -Destination $destPath -Force
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
            Artist   = $artist
            Title    = $title
            FlacFile = (Split-Path $filepath -Leaf)
            Dossier  = $dossier
            Dest     = $destPath
            Note     = ''
        })
        $deployed++
    }
}

# --- write outputs ---------------------------------------------------------

$report   | Export-Csv -LiteralPath (Join-Path $OutputDir 'migration_report.csv') -NoTypeInformation -Encoding UTF8
$unmapped | Export-Csv -LiteralPath (Join-Path $OutputDir 'unmapped.csv') -NoTypeInformation -Encoding UTF8

Write-Host ('=' * 70)
Write-Host ('Index rows       : {0}' -f $indexRows.Count)
Write-Host ('Deployed         : {0}' -f $deployed)
Write-Host ('Already existed  : {0}' -f $alreadyExisting)
Write-Host ('Skipped (fake)   : {0}' -f $skippedFake)
Write-Host ('Not downloaded   : {0}' -f $failedDl)
Write-Host ('File missing     : {0}' -f $missingFile)
Write-Host ('Unmapped         : {0}' -f $unmapped.Count)
Write-Host ('Report           : {0}' -f (Join-Path $OutputDir 'migration_report.csv'))
