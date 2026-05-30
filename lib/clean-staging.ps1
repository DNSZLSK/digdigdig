<#
.SYNOPSIS
    Apply cleanup actions to staging based on the audit CSV.

.DESCRIPTION
    Two operations, both gated by -Apply (dry-run by default) :

      - Quarantine SUSPECT files : MOVE them out of staging into a _rejected/
        folder. Reversible (nothing is destroyed) - they just can't be verified
        or deployed, and sit there for review. A _quarantine_log.csv records
        why each one was rejected.

      - Rename OK/PARTIAL files that have bad names (e.g. "-.wav") to the proper
        "Artist - Title" from the index.

    PARTIAL files are deliberately LEFT in place : they are the "review" bucket
    (full recall but extra words, short titles, mid-range recall). The deploy
    gate (route-files.ps1 -AuditCsv) already keeps anything that is not Status=OK
    off the target, so PARTIAL never reaches the library unattended.

    SUSPECT = confident wrong recording (wrong version, duration outlier, tag
    mismatch, recall below threshold).

.PARAMETER AuditCsv
    Output of audit-staging.ps1.

.PARAMETER Apply
    Without this flag, prints what would happen but changes nothing.

.PARAMETER QuarantineDir
    Where SUSPECT files are moved. Default: <staging>\_rejected, derived from the
    audit rows' FilePath.

.PARAMETER RenameBadNames
    Rename files whose BadName=True but a proper artist+title exists in the
    index. Default: on.

.PARAMETER DeleteSuspects
    Hard-DELETE SUSPECT files instead of quarantining them. Default: off.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$AuditCsv,
    [switch]$Apply,
    [string]$QuarantineDir = '',
    [bool]$RenameBadNames = $true,
    [switch]$DeleteSuspects
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $AuditCsv)) {
    throw "Audit CSV not found : $AuditCsv"
}

$audit = Import-Csv -LiteralPath $AuditCsv -Encoding UTF8

# Derive the quarantine dir from the staging root if not given explicitly.
if (-not $QuarantineDir) {
    $firstPath = ($audit | Where-Object { $_.FilePath } | Select-Object -First 1).FilePath
    if ($firstPath) {
        $QuarantineDir = Join-Path (Split-Path $firstPath -Parent) '_rejected'
    } else {
        $QuarantineDir = '.\staging\_rejected'
    }
}

function Get-UniquePath {
    param([string]$dir, [string]$name)
    $base = [System.IO.Path]::GetFileNameWithoutExtension($name)
    $ext  = [System.IO.Path]::GetExtension($name)
    $candidate = Join-Path $dir $name
    $i = 1
    while (Test-Path -LiteralPath $candidate) {
        $candidate = Join-Path $dir ("{0} ({1}){2}" -f $base, $i, $ext)
        $i++
    }
    return $candidate
}

$mode = if ($Apply) { 'APPLY' } else { 'DRY-RUN' }
$verb = if ($DeleteSuspects) { 'delete' } else { "quarantine -> $QuarantineDir" }
Write-Host "Mode        : $mode"
Write-Host "Suspect act : $verb"
Write-Host "Audit rows  : $($audit.Count)"

$quarantined = 0
$deleted = 0
$renamed = 0
$skipped = 0
$qlog = New-Object System.Collections.Generic.List[object]
$ts = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')

foreach ($row in $audit) {
    # Coerce string flags from CSV to boolean
    $badName = ($row.BadName -eq 'True' -or $row.BadName -eq $true)

    # ---- SUSPECT : quarantine (or hard-delete) --------------------------
    if ($row.Status -eq 'SUSPECT') {
        if (-not (Test-Path -LiteralPath $row.FilePath)) {
            Write-Host "  [SKIP] already gone : $($row.File)"
            $skipped++
            continue
        }

        if ($DeleteSuspects) {
            if ($Apply) {
                Remove-Item -LiteralPath $row.FilePath -Force
                Write-Host "  [DEL]  $($row.File)  ($($row.Reason))"
            } else {
                Write-Host "  [DRY-DEL]  $($row.File)  ($($row.Reason))"
            }
            $deleted++
            continue
        }

        # Quarantine = move. Give bad-named files a readable name if we can.
        $ext = [System.IO.Path]::GetExtension($row.File)
        $leaf = if ($badName -and $row.Requested) {
            (($row.Requested + $ext) -replace '[\\/:*?"<>|]', '_')
        } else {
            $row.File
        }

        if ($Apply) {
            if (-not (Test-Path -LiteralPath $QuarantineDir)) {
                New-Item -ItemType Directory -Force -Path $QuarantineDir | Out-Null
            }
            $dest = Get-UniquePath -dir $QuarantineDir -name $leaf
            Move-Item -LiteralPath $row.FilePath -Destination $dest -Force
            Write-Host "  [QUAR] $($row.File) -> $(Split-Path $dest -Leaf)  ($($row.Reason))"
        } else {
            Write-Host "  [DRY-QUAR] $($row.File) -> $leaf  ($($row.Reason))"
        }
        $qlog.Add([pscustomobject]@{
            When      = $ts
            File      = $row.File
            Requested = $row.Requested
            Status    = $row.Status
            Reason    = $row.Reason
            From      = $row.FilePath
        })
        $quarantined++
        continue
    }

    # ---- rename bad names on OK / PARTIAL -------------------------------
    if ($RenameBadNames -and $badName -and $row.Status -in @('OK', 'PARTIAL') -and $row.Requested) {
        $ext = [System.IO.Path]::GetExtension($row.File)
        $newName = "$($row.Requested)$ext"
        $newName = $newName -replace '[\\/:*?"<>|]', '_'
        $dir = Split-Path $row.FilePath -Parent
        $newPath = Join-Path $dir $newName
        if (Test-Path -LiteralPath $newPath) {
            Write-Host "  [SKIP] collision, $newName already exists"
            $skipped++
            continue
        }
        if ($Apply) {
            Rename-Item -LiteralPath $row.FilePath -NewName $newName
            Write-Host "  [REN]  $($row.File) -> $newName"
        } else {
            Write-Host "  [DRY-REN]  $($row.File) -> $newName"
        }
        $renamed++
        continue
    }
}

# Persist the quarantine log so rejected files can be reviewed / restored.
if ($Apply -and $qlog.Count -gt 0) {
    if (-not (Test-Path -LiteralPath $QuarantineDir)) {
        New-Item -ItemType Directory -Force -Path $QuarantineDir | Out-Null
    }
    $logPath = Join-Path $QuarantineDir '_quarantine_log.csv'
    $existing = if (Test-Path -LiteralPath $logPath) { @(Import-Csv -LiteralPath $logPath -Encoding UTF8) } else { @() }
    ($existing + $qlog) | Export-Csv -LiteralPath $logPath -NoTypeInformation -Encoding UTF8
}

Write-Host ('-' * 60)
Write-Host ('Mode        : {0}' -f $mode)
Write-Host ('Quarantined : {0}' -f $quarantined)
Write-Host ('Deleted     : {0}' -f $deleted)
Write-Host ('Renamed     : {0}' -f $renamed)
Write-Host ('Skipped     : {0}' -f $skipped)
if (-not $Apply) {
    Write-Host ''
    Write-Host 'Run again with -Apply to actually perform the changes.'
}
