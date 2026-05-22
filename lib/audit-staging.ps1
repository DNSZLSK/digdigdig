<#
.SYNOPSIS
    Score each downloaded file vs what was actually requested, output an audit CSV.

.DESCRIPTION
    For each audio file in the staging dir, looks up the requested artist/title
    from sldl's _index.csv (or an archived copy), then computes a similarity
    score based on word overlap.

    Score interpretation:
      >= 0.7 : OK (high confidence the right track was downloaded)
      0.4-0.7 : PARTIAL (probably same track, minor naming differences)
      <  0.4 : SUSPECT (likely wrong track or junk filename like "-.wav")

    Multiple index files can be passed (e.g. one per sldl run) - rows are
    merged with the most recent winning on collision.

.PARAMETER StagingDir
    Directory containing the downloaded audio files.

.PARAMETER IndexFiles
    One or more sldl _index.csv files. Defaults to <StagingDir>\sldl_input\_index.csv.

.PARAMETER OutputCsv
    Where to write the audit (default: outputs\staging_audit.csv).
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$StagingDir,
    [string[]]$IndexFiles = @(),
    [string]$OutputCsv = '.\outputs\staging_audit.csv'
)

$ErrorActionPreference = 'Stop'

if (-not $IndexFiles -or $IndexFiles.Count -eq 0) {
    $default = Join-Path $StagingDir 'sldl_input\_index.csv'
    if (Test-Path -LiteralPath $default) { $IndexFiles = @($default) }
}

# --- merge all index files into one path -> row map ----------------------

$pathMap = @{}
foreach ($idx in $IndexFiles) {
    if (-not (Test-Path -LiteralPath $idx)) {
        Write-Warning "Index not found, skipping : $idx"
        continue
    }
    $rows = Import-Csv -LiteralPath $idx -Encoding UTF8
    foreach ($r in $rows) {
        if ($r.filepath) {
            $pathMap[$r.filepath] = $r
        }
    }
}

function Strip-Punct {
    param([string]$s)
    return ($s.ToLower() -replace '[^a-z0-9\s]', ' ' -replace '\s+', ' ').Trim()
}

$stopWords = @('the','and','feat','with','mix','remix','edit','club','original',
               'extended','vocal','version','live','premiere','featuring','main',
               'dub','long','short','radio','pres','presents','rmx','ver','vol')

$results = New-Object System.Collections.Generic.List[object]
$files = Get-ChildItem -LiteralPath $StagingDir -File | Where-Object { $_.Extension -in '.flac', '.wav', '.WAV', '.aiff', '.aif' }

foreach ($f in $files) {
    $idxRow = $pathMap[$f.FullName]
    $base = [System.IO.Path]::GetFileNameWithoutExtension($f.Name)
    $isBadName = ($base -eq '-') -or ($base.Trim() -eq '') -or ($base -match '^[\-_\s]+$')

    if (-not $idxRow) {
        $results.Add([pscustomobject]@{
            Score    = -1
            Status   = 'NO_INDEX_ENTRY'
            File     = $f.Name
            Requested= ''
            FilePath = $f.FullName
            BadName  = $isBadName
        })
        continue
    }

    $requested = "$($idxRow.artist) - $($idxRow.title)".Trim().Trim('-').Trim()
    $reqText = Strip-Punct $requested
    $fileText = Strip-Punct $base
    $reqWords = $reqText -split '\s+' | Where-Object { $_.Length -gt 2 -and $_ -notin $stopWords }

    if (-not $reqWords -or $reqWords.Count -eq 0) {
        $results.Add([pscustomobject]@{
            Score    = -1
            Status   = 'NO_MEANINGFUL_WORDS'
            File     = $f.Name
            Requested= $requested
            FilePath = $f.FullName
            BadName  = $isBadName
        })
        continue
    }

    $matched = 0
    foreach ($w in $reqWords) {
        if ($fileText -match "\b$([regex]::Escape($w))\b") { $matched++ }
    }
    $score = [math]::Round($matched / $reqWords.Count, 2)

    $status = if ($score -ge 0.7) { 'OK' }
              elseif ($score -ge 0.4) { 'PARTIAL' }
              else { 'SUSPECT' }

    $results.Add([pscustomobject]@{
        Score    = $score
        Status   = $status
        File     = $f.Name
        Requested= $requested
        FilePath = $f.FullName
        BadName  = $isBadName
    })
}

# Ensure output dir exists
$outDir = Split-Path $OutputCsv -Parent
if ($outDir -and -not (Test-Path -LiteralPath $outDir)) {
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null
}
$results | Export-Csv -LiteralPath $OutputCsv -NoTypeInformation -Encoding UTF8

Write-Host ('Audited {0} files -> {1}' -f $files.Count, $OutputCsv)
$results | Group-Object Status | Sort-Object Count -Descending | ForEach-Object {
    Write-Host ('  {0,-20} : {1}' -f $_.Name, $_.Count)
}
