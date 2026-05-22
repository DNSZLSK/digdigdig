<#
.SYNOPSIS
    Retry sldl with query variants for tracks that were not-found or detected as fake.

.DESCRIPTION
    Inputs:
      - MissCsv : CSV of misses (cols: Artist,Title,Dossier,OrigFile) - from parse of sldl log
      - MapJson : same routing map as Phase F
      - SldlExe : path to sldl.exe
      - SldlConfig : path to sldl.conf
      - Profile : sldl profile to use (default 'lossless')
      - SoulseekUser / SoulseekPass
      - StagingRetryDir : where to drop retried downloads (e.g. staging\retry\)

    For each row, generates up to 4 query variants and tries them one-by-one
    via sldl in single-track mode (search-string input). Stops at first hit.

    Variants generated, in order:
      1. "Artist Title flac"             (canonical)
      2. "Artist Title-short flac"       (strip "Original Mix", "Extended Mix", etc.)
      3. "Title Artist flac"             (inverted - some CSV rows have this swapped)
      4. "Title flac"                    (artist-less, last resort)
#>
param(
    [Parameter(Mandatory)][string]$MissCsv,
    [Parameter(Mandatory)][string]$MapJson,
    [Parameter(Mandatory)][string]$SldlExe,
    [Parameter(Mandatory)][string]$SldlConfig,
    [Parameter(Mandatory)][string]$SoulseekUser,
    [Parameter(Mandatory)][string]$SoulseekPass,
    [Parameter(Mandatory)][string]$StagingRetryDir,
    [string]$Profile = 'lossless',
    [int]$MaxRetries = 3
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $MissCsv)) { throw "Miss CSV not found : $MissCsv" }
if (-not (Test-Path -LiteralPath $SldlExe)) { throw "sldl.exe not found : $SldlExe" }
if (-not (Test-Path -LiteralPath $StagingRetryDir)) {
    New-Item -ItemType Directory -Force -Path $StagingRetryDir | Out-Null
}

function Clean-Term {
    param([string]$s)
    if (-not $s) { return '' }
    $s = $s -replace '\[[A-Za-z]+\d+\]', ''
    $s = $s -replace '\[\d+\]', ''
    $s = $s -replace "['`"]", ''
    return ($s -replace '\s+', ' ').Trim()
}

function New-Variants {
    param([string]$Artist, [string]$Title)
    $a = Clean-Term $Artist
    $t = Clean-Term $Title
    $tShort = $t -replace '\s*\((original|club|extended|radio|long|short|dub|vocal)\s*(mix|edit|version|remix)?\)', ''
    $tShort = ($tShort -replace '\s+', ' ').Trim()

    $variants = @()
    if ($a -and $t) {
        $variants += "$a $t flac"
        if ($tShort -and $tShort -ne $t) { $variants += "$a $tShort flac" }
        $variants += "$t $a flac"
    }
    if ($t) { $variants += "$t flac" }
    return $variants | Select-Object -First $MaxRetries
}

$misses = Import-Csv -LiteralPath $MissCsv -Encoding UTF8
Write-Host "Retrying $($misses.Count) missed tracks..."

$results = New-Object System.Collections.Generic.List[object]
$idx = 0
$wins = 0
foreach ($m in $misses) {
    $idx++
    $variants = New-Variants -Artist $m.Artist -Title $m.Title
    if ($variants.Count -eq 0) {
        $results.Add([pscustomobject]@{
            Artist = $m.Artist
            Title = $m.Title
            Variant = ''
            Result = 'NO_VARIANT_POSSIBLE'
        })
        continue
    }

    $found = $false
    $tried = @()
    foreach ($v in $variants) {
        Write-Host "  [$idx/$($misses.Count)] Try : '$v'"
        $tried += $v
        $sldlArgs = @(
            $v
            '--input-type', 'string'
            '--user', $SoulseekUser
            '--pass', $SoulseekPass
            '--config', $SldlConfig
            '--profile', $Profile
            '--path', $StagingRetryDir
            '-n', '1'
        )
        $sldlOutput = & $SldlExe @sldlArgs 2>&1
        if ($LASTEXITCODE -eq 0 -and ($sldlOutput -match 'Succeeded|Downloaded\s+1')) {
            $results.Add([pscustomobject]@{
                Artist = $m.Artist
                Title = $m.Title
                Variant = $v
                Result = 'HIT'
                Tried = ($tried -join ' | ')
            })
            $wins++
            $found = $true
            break
        }
    }
    if (-not $found) {
        $results.Add([pscustomobject]@{
            Artist = $m.Artist
            Title = $m.Title
            Variant = ''
            Result = 'STILL_NOT_FOUND'
            Tried = ($tried -join ' | ')
        })
    }
}

$results | Export-Csv -LiteralPath (Join-Path (Split-Path $MissCsv -Parent) 'retry_results.csv') -NoTypeInformation -Encoding UTF8
Write-Host ""
Write-Host ('Retry done : {0} hits out of {1} attempts ({2:N1} %)' -f $wins, $misses.Count, ($wins * 100.0 / [Math]::Max(1, $misses.Count)))
