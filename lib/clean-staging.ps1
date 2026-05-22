<#
.SYNOPSIS
    Apply cleanup actions to staging based on the audit CSV.

.DESCRIPTION
    Two operations, both opt-in :
      - Delete SUSPECT files with bad filenames (e.g. "-.wav") - obvious junk
      - Rename PARTIAL/OK files that have ugly names (no artist) using the index

    Always defaults to DryRun. Pass -Apply to actually modify files.

.PARAMETER AuditCsv
    Output of audit-staging.ps1.

.PARAMETER Apply
    Without this flag, prints what would happen but changes nothing.

.PARAMETER DeleteSuspectsBadName
    Delete files where Status=SUSPECT AND BadName=True. Default: on.

.PARAMETER RenameBadNames
    Rename files whose BadName=True but a proper artist+title exists in the
    index. Default: on.

.PARAMETER DeleteSuspectsAll
    Delete ALL suspect files, not just badly-named ones. Default: off
    (off because some SUSPECT may be partial valid matches we want to review).
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$AuditCsv,
    [switch]$Apply,
    [bool]$DeleteSuspectsBadName = $true,
    [bool]$RenameBadNames = $true,
    [switch]$DeleteSuspectsAll
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $AuditCsv)) {
    throw "Audit CSV not found : $AuditCsv"
}

$audit = Import-Csv -LiteralPath $AuditCsv -Encoding UTF8

$mode = if ($Apply) { 'APPLY' } else { 'DRY-RUN' }
Write-Host "Mode : $mode"
Write-Host "Audit rows : $($audit.Count)"

$deleted = 0
$renamed = 0
$skipped = 0

foreach ($row in $audit) {
    # Coerce string flags from CSV to boolean
    $badName = ($row.BadName -eq 'True' -or $row.BadName -eq $true)

    # ---- delete obvious junk -------------------------------------------
    if ($DeleteSuspectsBadName -and $row.Status -eq 'SUSPECT' -and $badName) {
        if (-not (Test-Path -LiteralPath $row.FilePath)) {
            Write-Host "  [SKIP] already gone : $($row.File)"
            $skipped++
            continue
        }
        if ($Apply) {
            Remove-Item -LiteralPath $row.FilePath -Force
            Write-Host "  [DEL]  $($row.File) (suspect + bad name, requested: $($row.Requested))"
        } else {
            Write-Host "  [DRY-DEL]  $($row.File) (suspect + bad name, requested: $($row.Requested))"
        }
        $deleted++
        continue
    }

    if ($DeleteSuspectsAll -and $row.Status -eq 'SUSPECT') {
        if (-not (Test-Path -LiteralPath $row.FilePath)) { $skipped++; continue }
        if ($Apply) {
            Remove-Item -LiteralPath $row.FilePath -Force
            Write-Host "  [DEL]  $($row.File) (suspect, requested: $($row.Requested))"
        } else {
            Write-Host "  [DRY-DEL]  $($row.File) (suspect, requested: $($row.Requested))"
        }
        $deleted++
        continue
    }

    # ---- rename bad names -----------------------------------------------
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

Write-Host ('-' * 60)
Write-Host ('Mode    : {0}' -f $mode)
Write-Host ('Deleted : {0}' -f $deleted)
Write-Host ('Renamed : {0}' -f $renamed)
Write-Host ('Skipped : {0}' -f $skipped)
if (-not $Apply) {
    Write-Host ''
    Write-Host 'Run again with -Apply to actually perform the changes.'
}
