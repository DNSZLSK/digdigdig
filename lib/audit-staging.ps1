<#
.SYNOPSIS
    Score each downloaded file vs what was actually requested.

.DESCRIPTION
    For each audio file in the staging dir, look up the requested track from
    sldl's _index.csv (or an archived copy) and score on three independent axes:

      - Artist token coverage : (artistTokens in filename) / artistTokens
      - Title token coverage  : (titleTokens  in filename) / titleTokens
      - Duration check        : ffprobe-measured duration vs expected length
                                (or outlier flag when no expected length)

    Status reflects the weakest axis :
      OK                  : artist >= OkThreshold AND title >= OkThreshold AND duration ok
      PARTIAL             : both scores >= PartialThreshold AND duration ok
      SUSPECT             : any axis fails
      NO_INDEX_ENTRY      : file present but no row in any index
                            (still flagged SUSPECT if duration is an outlier)
      NO_MEANINGFUL_WORDS : index row exists but artist+title produce zero usable tokens

    Multiple index files can be passed (e.g. one per sldl run) - rows are
    merged with the most recent winning on collision.

.PARAMETER StagingDir
    Directory containing the downloaded audio files.

.PARAMETER IndexFiles
    One or more sldl _index.csv files. Defaults to <StagingDir>\sldl_input\_index.csv.

.PARAMETER OutputCsv
    Where to write the audit (default: outputs\staging_audit.csv).

.PARAMETER OkThreshold
    Minimum artist+title score for OK status (default 0.8).

.PARAMETER PartialThreshold
    Minimum artist+title score for PARTIAL status (default 0.5). Below this = SUSPECT.

.PARAMETER DurationTolerancePct
    When expected length is known, allow this percent deviation (default 15).

.PARAMETER MinDurationOutlier
    When no expected length, flag files shorter than this many seconds (default 60).

.PARAMETER MaxDurationOutlier
    When no expected length, flag files longer than this many seconds (default 900).
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$StagingDir,
    [string[]]$IndexFiles = @(),
    [string]$OutputCsv = '.\outputs\staging_audit.csv',
    [double]$OkThreshold = 0.8,
    [double]$PartialThreshold = 0.5,
    [double]$DurationTolerancePct = 15,
    [int]$MinDurationOutlier = 90,
    [int]$MaxDurationOutlier = 900,
    [double]$TagMismatchThreshold = 0.3
)

$ErrorActionPreference = 'Stop'

if (-not $IndexFiles -or $IndexFiles.Count -eq 0) {
    $default = Join-Path $StagingDir 'sldl_input\_index.csv'
    if (Test-Path -LiteralPath $default) { $IndexFiles = @($default) }
}

# --- merge index files into one path -> row map ---------------------------
$pathMap = @{}
foreach ($idx in $IndexFiles) {
    if (-not (Test-Path -LiteralPath $idx)) {
        Write-Warning "Index not found, skipping : $idx"
        continue
    }
    $rows = Import-Csv -LiteralPath $idx -Encoding UTF8
    foreach ($r in $rows) {
        if ($r.filepath) { $pathMap[$r.filepath] = $r }
    }
}

$stopWords = @('the','and','feat','with','mix','remix','edit','club','original',
               'extended','vocal','version','live','premiere','featuring','main',
               'dub','long','short','radio','pres','presents','rmx','ver','vol')

function Get-Tokens {
    param([string]$s, [int]$MinLen = 3)
    if (-not $s) { return @() }
    $clean = ($s.ToLower() -replace '[^a-z0-9\s]', ' ' -replace '\s+', ' ').Trim()
    return @($clean -split '\s+' | Where-Object { $_.Length -ge $MinLen -and $_ -notin $stopWords })
}

function Get-TokenCoverage {
    # Fraction of $req tokens that appear in $file tokens.
    # Returns -1 when $req is empty (axis not checkable).
    param([string[]]$req, [string[]]$file)
    if (-not $req -or $req.Count -eq 0) { return -1 }
    $matched = 0
    foreach ($w in $req) {
        if ($file -contains $w) { $matched++ }
    }
    return [math]::Round($matched / $req.Count, 2)
}

function Get-AudioInfo {
    # Returns @{Duration; Artist; Title; Album} with -1 / empty defaults.
    # One ffprobe call extracts both duration and metadata tags.
    param([string]$path)
    $info = [pscustomobject]@{ Duration = -1.0; Artist = ''; Title = ''; Album = '' }
    try {
        $ffArgs = @(
            '-v', 'error',
            '-show_entries', 'format=duration:format_tags=artist,title,album',
            '-of', 'json',
            '--', $path
        )
        $raw = & ffprobe @ffArgs 2>$null
        if (-not $raw) { return $info }
        $json = $raw | Out-String | ConvertFrom-Json
        if ($json.format.duration -and ($json.format.duration -match '^[\d.]+$')) {
            $info.Duration = [double]$json.format.duration
        }
        if ($json.format.tags) {
            if ($json.format.tags.artist) { $info.Artist = [string]$json.format.tags.artist }
            if ($json.format.tags.title)  { $info.Title  = [string]$json.format.tags.title  }
            if ($json.format.tags.album)  { $info.Album  = [string]$json.format.tags.album  }
        }
    } catch {}
    return $info
}

$files = Get-ChildItem -LiteralPath $StagingDir -File | Where-Object {
    $_.Extension -in '.flac', '.wav', '.WAV', '.aiff', '.aif'
}

Write-Host ('Analyzing {0} files (ffprobe per file, this can take a few minutes)...' -f $files.Count)

$results = New-Object System.Collections.Generic.List[object]
$progress = 0

foreach ($f in $files) {
    $progress++
    if ($progress % 25 -eq 0) {
        Write-Host ('  ... {0}/{1}' -f $progress, $files.Count)
    }

    $idxRow = $pathMap[$f.FullName]
    $base = [System.IO.Path]::GetFileNameWithoutExtension($f.Name)
    $isBadName = ($base -eq '-') -or ($base.Trim() -eq '') -or ($base -match '^[\-_\s]+$')

    $audioInfo = Get-AudioInfo -path $f.FullName
    $duration = $audioInfo.Duration
    $expectedDur = -1.0

    # --- no index match : orphaned file -----------------------------------
    if (-not $idxRow) {
        $status = 'NO_INDEX_ENTRY'
        $reason = 'no index row'
        if ($duration -ge 0 -and ($duration -lt $MinDurationOutlier -or $duration -gt $MaxDurationOutlier)) {
            $status = 'SUSPECT'
            $reason = "no_index + dur_outlier ($([math]::Round($duration,1))s)"
        }
        $results.Add([pscustomobject]@{
            Score            = -1
            Status           = $status
            File             = $f.Name
            Requested        = ''
            FilePath         = $f.FullName
            BadName          = $isBadName
            Duration         = $duration
            ExpectedDuration = -1
            TitleScore       = -1
            ArtistScore      = -1
            TagArtist        = $audioInfo.Artist
            TagTitle         = $audioInfo.Title
            Reason           = $reason
        })
        continue
    }

    $requested = "$($idxRow.artist) - $($idxRow.title)".Trim().Trim('-').Trim()

    # sldl _index.csv stores expected length in 'length' column ('-1' when unknown)
    if ($idxRow.length -and $idxRow.length -ne '-1') {
        try { $expectedDur = [double]$idxRow.length } catch { $expectedDur = -1.0 }
    }

    $titleTokens  = Get-Tokens -s $idxRow.title  -MinLen 3
    $artistTokens = Get-Tokens -s $idxRow.artist -MinLen 2
    $fileTokens   = Get-Tokens -s $base          -MinLen 2

    if ($titleTokens.Count -eq 0 -and $artistTokens.Count -eq 0) {
        $results.Add([pscustomobject]@{
            Score            = -1
            Status           = 'NO_MEANINGFUL_WORDS'
            File             = $f.Name
            Requested        = $requested
            FilePath         = $f.FullName
            BadName          = $isBadName
            Duration         = $duration
            ExpectedDuration = $expectedDur
            TitleScore       = -1
            ArtistScore      = -1
            TagArtist        = $audioInfo.Artist
            TagTitle         = $audioInfo.Title
            Reason           = 'index row has no meaningful tokens'
        })
        continue
    }

    $titleScore  = Get-TokenCoverage -req $titleTokens  -file $fileTokens
    $artistScore = Get-TokenCoverage -req $artistTokens -file $fileTokens

    # Treat -1 (no tokens on that axis) as a pass so single-axis tracks aren't penalised
    $effectiveTitle  = if ($titleScore  -ge 0) { $titleScore  } else { 1.0 }
    $effectiveArtist = if ($artistScore -ge 0) { $artistScore } else { 1.0 }

    # Duration check
    $durationOk = $true
    $durReason = ''
    if ($duration -lt 0) {
        $durReason = 'dur_unknown'
    } elseif ($expectedDur -gt 0) {
        $tol = $expectedDur * ($DurationTolerancePct / 100.0)
        if ([math]::Abs($duration - $expectedDur) -gt $tol) {
            $durationOk = $false
            $durReason = ("dur_mismatch ({0:N1}s vs expected {1:N1}s)" -f $duration, $expectedDur)
        }
    } else {
        if ($duration -lt $MinDurationOutlier) {
            $durationOk = $false
            $durReason = ("dur_too_short ({0:N1}s)" -f $duration)
        } elseif ($duration -gt $MaxDurationOutlier) {
            $durationOk = $false
            $durReason = ("dur_too_long ({0:N1}s)" -f $duration)
        }
    }

    # Tag cross-check : if audio tags exist and disagree strongly with the
    # request, the file is probably the wrong audio renamed to look right.
    $tagSuspect = $false
    $tagReason = ''
    if ($audioInfo.Artist -or $audioInfo.Title) {
        $tagArtistTokens = Get-Tokens -s $audioInfo.Artist -MinLen 2
        $tagTitleTokens  = Get-Tokens -s $audioInfo.Title  -MinLen 3
        $tagArtistScore = Get-TokenCoverage -req $artistTokens -file $tagArtistTokens
        $tagTitleScore  = Get-TokenCoverage -req $titleTokens  -file $tagTitleTokens
        $effTagArtist = if ($tagArtistScore -ge 0) { $tagArtistScore } else { 0 }
        $effTagTitle  = if ($tagTitleScore  -ge 0) { $tagTitleScore  } else { 0 }
        if (([math]::Max($effTagArtist, $effTagTitle)) -lt $TagMismatchThreshold) {
            $tagSuspect = $true
            $tagReason = ("tag_mismatch (tags='{0} - {1}')" -f $audioInfo.Artist, $audioInfo.Title)
        }
    }

    # Aggregate
    $minScore = [math]::Min($effectiveTitle, $effectiveArtist)

    $reasons = New-Object System.Collections.Generic.List[string]
    if (-not $durationOk) { $reasons.Add($durReason) }
    if ($tagSuspect)      { $reasons.Add($tagReason) }
    if ($effectiveArtist -lt $PartialThreshold) { $reasons.Add("artist=$effectiveArtist") }
    if ($effectiveTitle  -lt $PartialThreshold) { $reasons.Add("title=$effectiveTitle") }

    if (-not $durationOk -or $tagSuspect) {
        $status = 'SUSPECT'
    } elseif ($effectiveArtist -ge $OkThreshold -and $effectiveTitle -ge $OkThreshold) {
        $status = 'OK'
    } elseif ($effectiveArtist -ge $PartialThreshold -and $effectiveTitle -ge $PartialThreshold) {
        $status = 'PARTIAL'
        if ($reasons.Count -eq 0) {
            $reasons.Add("partial (a=$effectiveArtist, t=$effectiveTitle)")
        }
    } else {
        $status = 'SUSPECT'
    }

    if ($durReason -and $durationOk -and $reasons.Count -eq 0) {
        $reasons.Add($durReason)
    }

    $results.Add([pscustomobject]@{
        Score            = $minScore
        Status           = $status
        File             = $f.Name
        Requested        = $requested
        FilePath         = $f.FullName
        BadName          = $isBadName
        Duration         = $duration
        ExpectedDuration = $expectedDur
        TitleScore       = $effectiveTitle
        ArtistScore      = $effectiveArtist
        TagArtist        = $audioInfo.Artist
        TagTitle         = $audioInfo.Title
        Reason           = ($reasons -join '; ')
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
