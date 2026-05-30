<#
.SYNOPSIS
    Score each downloaded file vs what was actually requested.

.DESCRIPTION
    For each audio file in the staging dir, look up the requested track from
    sldl's _index.csv (or an archived copy) and decide whether the file matches
    the *complete* requested title - not just "are the requested words present".

    Five checks, in increasing strictness :

      - Artist token coverage  : (artistTokens in filename) / artistTokens          [recall]
      - Title token coverage   : (titleTokens  in filename) / titleTokens            [recall]
      - Title precision        : extra significant words in the file's title that we
                                 did NOT ask for (remixer names, extra title words). [precision]
      - Version signature      : the version qualifier we asked for (Original / a
                                 specific Remix / Extended / Radio ...) must equal
                                 the file's version qualifier.                        [version]
      - Duration + tag sanity  : ffprobe duration vs expected/outlier, embedded tags.

    Status (severity: SUSPECT = quarantine, PARTIAL = keep for review, never deploy) :
      OK                  : artist>=Ok AND title-recall>=Ok AND 0 extra words AND
                            version matches AND duration ok AND tags ok
      PARTIAL             : a "review" bucket - full recall but extra words (often
                            just messy album/rip naming on the right audio), OR an
                            uncorroborated short title, OR mid-range recall.
                            Held in staging, never auto-deployed.
      SUSPECT             : confident "wrong recording" - wrong version, duration
                            outlier, tag mismatch, or recall below Partial.
      NO_INDEX_ENTRY      : file present but no row in any index
      NO_MEANINGFUL_WORDS : index row exists but artist+title produce zero tokens

    "Complete title" guard rails to avoid false rejects :
      - "(Original Mix)" / "(Main)" / "(Album Version)" == original (empty version).
      - feat. / ft. / featuring tails are dropped (guest artist, never "extra").
      - format / year / label / catalogue / web tokens are treated as noise.

.PARAMETER StagingDir
    Directory containing the downloaded audio files.

.PARAMETER IndexFiles
    One or more sldl _index.csv files. Defaults to <StagingDir>\sldl_input\_index.csv.

.PARAMETER OutputCsv
    Where to write the audit (default: outputs\staging_audit.csv).

.PARAMETER OkThreshold
    Minimum artist+title recall for OK status (default 0.8).

.PARAMETER PartialThreshold
    Minimum artist+title recall for PARTIAL status (default 0.5). Below this = SUSPECT.

.PARAMETER DurationTolerancePct
    When expected length is known, allow this percent deviation (default 10).

.PARAMETER MinDurationOutlier
    When no expected length, flag files shorter than this many seconds (default 90).

.PARAMETER MaxDurationOutlier
    When no expected length, flag files longer than this many seconds (default 720).

.PARAMETER TagMismatchThreshold
    Embedded tags below this coverage on both axes => tag_mismatch (default 0.3).

.PARAMETER MaxExtraWords
    Unexpected significant words in the file's title at/above which the precision
    check fires (default 1). On its own it downgrades a full-recall match to
    PARTIAL (review) - see SuspectExtraWords for when it escalates to SUSPECT.

.PARAMETER SuspectExtraWords
    Extra-word count at/above which the file is treated as a different object
    (compilation / megamix) -> SUSPECT instead of PARTIAL (default 3). A
    compilation/mix marker word in the title also forces SUSPECT.

.PARAMETER NoPrecisionCheck
    Disable the extra-words / precision check.

.PARAMETER NoVersionCheck
    Disable the version-signature check.

.PARAMETER NoShortTitleGuard
    Disable holding short-title matches at PARTIAL when uncorroborated.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$StagingDir,
    [string[]]$IndexFiles = @(),
    [string]$OutputCsv = '.\outputs\staging_audit.csv',
    [double]$OkThreshold = 0.8,
    [double]$PartialThreshold = 0.5,
    [double]$DurationTolerancePct = 10,
    [int]$MinDurationOutlier = 90,
    [int]$MaxDurationOutlier = 720,
    [double]$TagMismatchThreshold = 0.3,
    [int]$MaxExtraWords = 1,
    [int]$SuspectExtraWords = 3,
    [switch]$NoPrecisionCheck,
    [switch]$NoVersionCheck,
    [switch]$NoShortTitleGuard
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

# Structural / version words removed before recall+precision token matching.
# (Version identity is handled separately by Get-VersionKey, which does NOT
#  use this list - it must still see 'remix', 'edit', etc.)
$stopWords = @('the','and','feat','with','mix','remix','edit','club','original',
               'extended','vocal','version','live','premiere','featuring','main',
               'dub','long','short','radio','pres','presents','rmx','ver','vol')

# Benign tokens that should never count as "extra" words in a title.
$noiseWords = @('flac','wav','aiff','aif','mp3','aac','ogg','m4a',
                'web','vinyl','cd','cdr','cdq','ep','lp','single','va',
                'kbps','khz','hz','bit','hq','lossless','master','remaster','remastered',
                'www','com','net','org','rip','scene','promo','digital')

# Distinctive version qualifiers : two titles whose distinctive set differs are
# different versions. Neutral words (original/main/album/version/mix/remaster) are
# deliberately ABSENT here, so they normalise to "the original" (empty key).
$distinctiveVer = @('remix','rmx','rework','rwk','edit','reedit','redit','refix','flip',
                    'extended','radio','club','dub','vocal','instrumental','inst',
                    'acapella','acappella','acoustic','live','bootleg','boot',
                    'mashup','vip','demo')
$verCanon = @{
    'rmx'='remix'; 'rework'='remix'; 'rwk'='remix';
    'reedit'='edit'; 'redit'='edit'; 'refix'='edit';
    'inst'='instrumental'; 'acappella'='acapella'; 'boot'='bootleg'
}

# Title markers that mean "this is a bigger aggregate, not the single track asked
# for" (continuous DJ mix, compilation, megamix...). When the file title carries
# extra words AND one of these, escalate from PARTIAL to SUSPECT.
$mixMarkerRe = '\b(megamix|mega mix|continuous|dj ?mix|mixtape|non ?stop|sampler|compilation|full album|b2b|back to back|live set|essential mix|versus|podcast)\b'

function Remove-Diacritics {
    # "Andre" from "Andre" (fold combining marks) without literal accented chars.
    param([string]$s)
    if (-not $s) { return '' }
    $norm = $s.Normalize([System.Text.NormalizationForm]::FormD)
    $sb = New-Object System.Text.StringBuilder
    foreach ($c in $norm.ToCharArray()) {
        $cat = [System.Globalization.CharUnicodeInfo]::GetUnicodeCategory($c)
        if ($cat -ne [System.Globalization.UnicodeCategory]::NonSpacingMark) {
            [void]$sb.Append($c)
        }
    }
    return $sb.ToString().Normalize([System.Text.NormalizationForm]::FormC)
}

function Get-Tokens {
    param([string]$s, [int]$MinLen = 3)
    if (-not $s) { return ,@() }
    $s = Remove-Diacritics $s
    $clean = ($s.ToLower() -replace '[^a-z0-9\s]', ' ' -replace '\s+', ' ').Trim()
    # Leading comma stops PowerShell from unwrapping a single-element result into
    # a scalar on return (which would break array concatenation at the call site).
    return ,@($clean -split '\s+' | Where-Object { $_.Length -ge $MinLen -and $_ -notin $stopWords })
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

function Split-Name {
    # sldl writes "{artist} - {title}". Split on the FIRST ' - ' so a title that
    # itself contains ' - ' stays intact in the title portion.
    param([string]$base)
    $parts = $base -split ' - ', 2
    if ($parts.Count -eq 2) {
        return [pscustomobject]@{ Artist = $parts[0].Trim(); Title = $parts[1].Trim(); Split = $true }
    }
    return [pscustomobject]@{ Artist = ''; Title = $base.Trim(); Split = $false }
}

function Remove-FeatTail {
    # Drop "feat. X" / "ft X" / "featuring X" to the end (guest artist).
    param([string]$s)
    if (-not $s) { return '' }
    return ($s -replace '(?i)\s*[\(\[]?\b(feat\.?|ft\.?|featuring)\b.*$', '')
}

function Test-NoiseToken {
    param([string]$w)
    if ($w -match '^\d+$') { return $true }                              # pure digits / years / track no
    if (($w -match '\d') -and ($w -match '[a-z]') -and ($w.Length -le 8)) { return $true } # catalogue-ish (cat123, mp3, 320k)
    if ($w -in $noiseWords) { return $true }
    return $false
}

function Get-ExtraWords {
    # File-title tokens that we did NOT ask for and that are not benign noise.
    param([string[]]$fileTitleTokens, [string[]]$allowed)
    $extra = New-Object System.Collections.Generic.List[string]
    foreach ($w in $fileTitleTokens) {
        if ($allowed -contains $w) { continue }
        if (Test-NoiseToken $w)    { continue }
        $extra.Add($w)
    }
    return @($extra)
}

function Get-VersionKey {
    # Normalised, sorted set of DISTINCTIVE version qualifiers in a title.
    # "" == original. Does not use $stopWords (must still see remix/edit/...).
    param([string]$title)
    if (-not $title) { return '' }
    $t = (Remove-Diacritics $title).ToLower() -replace '[^a-z0-9\s]', ' '
    $keys = New-Object System.Collections.Generic.HashSet[string]
    foreach ($w in ($t -split '\s+')) {
        if (-not $w) { continue }
        if ($distinctiveVer -contains $w) {
            $c = if ($verCanon.ContainsKey($w)) { $verCanon[$w] } else { $w }
            [void]$keys.Add($c)
        }
    }
    return (($keys | Sort-Object) -join '+')
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
            TitlePrecision   = -1
            ExtraWords       = ''
            VersionMatch     = ''
            ReqVersion       = ''
            FileVersion      = ''
            ShortTitleRisk   = $false
            MixMarker        = $false
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
            TitlePrecision   = -1
            ExtraWords       = ''
            VersionMatch     = ''
            ReqVersion       = ''
            FileVersion      = ''
            ShortTitleRisk   = $false
            MixMarker        = $false
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

    # --- Precision : extra significant words in the file's title portion ----
    $split = Split-Name $base
    $fileTitleTokens = Get-Tokens -s (Remove-FeatTail $split.Title) -MinLen 3
    $allowed = @($titleTokens) + @($artistTokens)
    $extraWords = if ($NoPrecisionCheck) { @() } else { Get-ExtraWords -fileTitleTokens $fileTitleTokens -allowed $allowed }
    $extraCount = @($extraWords).Count
    $titlePrecision = if ($fileTitleTokens.Count -gt 0) {
        [math]::Round((($fileTitleTokens.Count - $extraCount) / $fileTitleTokens.Count), 2)
    } else { 1.0 }
    # Compilation / continuous-mix marker in the file title (escalates extra-words).
    $mixMarkerHit = (-not $NoPrecisionCheck) -and (((Remove-Diacritics $split.Title).ToLower()) -match $mixMarkerRe)

    # --- Version signature : must match what we asked for -------------------
    $reqVer  = Get-VersionKey -title $idxRow.title
    $fileVer = Get-VersionKey -title $split.Title
    $versionMatch = if ($NoVersionCheck) { $true } else { ($reqVer -eq $fileVer) }

    # Very short titles (single token both axes) are match magnets.
    $shortTitleRisk = ($titleTokens.Count -le 1 -and $artistTokens.Count -le 1)

    # --- Duration check -----------------------------------------------------
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

    # --- Tag cross-check : embedded tags from a different recording ---------
    # Catches (a) both tags way off the request, and (b) a tag that names a
    # DIFFERENT artist (present-but-wrong), even if the title tag happens to match.
    $tagSuspect = $false
    $tagReason = ''
    $tagCorroborated = $false
    if ($audioInfo.Artist -or $audioInfo.Title) {
        $tagArtistTokens = Get-Tokens -s $audioInfo.Artist -MinLen 2
        $tagTitleTokens  = Get-Tokens -s $audioInfo.Title  -MinLen 3
        $tagArtistScore = Get-TokenCoverage -req $artistTokens -file $tagArtistTokens
        $tagTitleScore  = Get-TokenCoverage -req $titleTokens  -file $tagTitleTokens
        $effTagArtist = if ($tagArtistScore -ge 0) { $tagArtistScore } else { 0 }
        $effTagTitle  = if ($tagTitleScore  -ge 0) { $tagTitleScore  } else { 0 }

        # (a) both axes terrible
        if (([math]::Max($effTagArtist, $effTagTitle)) -lt $TagMismatchThreshold) {
            $tagSuspect = $true
            $tagReason = ("tag_mismatch (tags='{0} - {1}')" -f $audioInfo.Artist, $audioInfo.Title)
        }
        # (b) artist tag present but names someone else, title tag not strong
        elseif ($audioInfo.Artist -and $tagArtistScore -eq 0 -and $effTagTitle -lt 0.8 -and $artistTokens.Count -gt 0) {
            $tagSuspect = $true
            $tagReason = ("tag_wrong_artist (tag artist='{0}')" -f $audioInfo.Artist)
        }

        if ($effTagArtist -ge 0.5 -or $effTagTitle -ge 0.5) { $tagCorroborated = $true }
    }
    $durCorroborated = ($expectedDur -gt 0 -and $durationOk)
    $corroborated = ($tagCorroborated -or $durCorroborated)

    # --- Aggregate ----------------------------------------------------------
    $minScore = [math]::Min($effectiveTitle, $effectiveArtist)

    $versionFail   = -not $versionMatch
    $titleExtraFail = (-not $NoPrecisionCheck) -and ($extraCount -ge $MaxExtraWords)

    $reasons = New-Object System.Collections.Generic.List[string]
    if (-not $durationOk) { $reasons.Add($durReason) }
    if ($tagSuspect)      { $reasons.Add($tagReason) }

    # Severity model :
    #   SUSPECT (-> quarantine) is reserved for confident "wrong recording" :
    #     duration outlier, tag mismatch, wrong version, or recall below Partial.
    #   PARTIAL (-> kept in staging, never auto-deployed, for manual review) covers
    #     ambiguous cases : extra words (often just messy album/rip naming on the
    #     correct audio), uncorroborated short titles, mid-range recall.
    if (-not $durationOk -or $tagSuspect) {
        $status = 'SUSPECT'
        if ($effectiveArtist -lt $PartialThreshold) { $reasons.Add("artist=$effectiveArtist") }
        if ($effectiveTitle  -lt $PartialThreshold) { $reasons.Add("title=$effectiveTitle") }
    }
    elseif ($versionFail) {
        $status = 'SUSPECT'
        $rv = if ($reqVer)  { $reqVer }  else { '(original)' }
        $fv = if ($fileVer) { $fileVer } else { '(original)' }
        $reasons.Add("version_mismatch (want '$rv' got '$fv')")
    }
    elseif ($effectiveArtist -ge $OkThreshold -and $effectiveTitle -ge $OkThreshold) {
        if ($titleExtraFail) {
            $ew = (@($extraWords) -join ',')
            if ($mixMarkerHit) {
                $status = 'SUSPECT'
                $reasons.Add("mix_marker + extra_words ($ew)")
            } elseif ($extraCount -ge $SuspectExtraWords) {
                $status = 'SUSPECT'
                $reasons.Add("many_extra_words ($ew)")
            } else {
                $status = 'PARTIAL'
                $reasons.Add("extra_words ($ew)")
            }
        }
        elseif ((-not $NoShortTitleGuard) -and $shortTitleRisk -and -not $corroborated) {
            $status = 'PARTIAL'
            $reasons.Add('short_title_unverified (no tag/duration corroboration)')
        } else {
            $status = 'OK'
        }
    }
    elseif ($effectiveArtist -ge $PartialThreshold -and $effectiveTitle -ge $PartialThreshold) {
        $status = 'PARTIAL'
        if ($effectiveArtist -lt $OkThreshold) { $reasons.Add("artist=$effectiveArtist") }
        if ($effectiveTitle  -lt $OkThreshold) { $reasons.Add("title=$effectiveTitle") }
        if ($titleExtraFail) { $reasons.Add("extra_words ($((@($extraWords) -join ',')))") }
        if ($reasons.Count -eq 0) { $reasons.Add("partial (a=$effectiveArtist, t=$effectiveTitle)") }
    }
    else {
        $status = 'SUSPECT'
        if ($effectiveArtist -lt $PartialThreshold) { $reasons.Add("artist=$effectiveArtist") }
        if ($effectiveTitle  -lt $PartialThreshold) { $reasons.Add("title=$effectiveTitle") }
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
        TitlePrecision   = $titlePrecision
        ExtraWords       = (@($extraWords) -join ',')
        VersionMatch     = $versionMatch
        ReqVersion       = $reqVer
        FileVersion      = $fileVer
        ShortTitleRisk   = $shortTitleRisk
        MixMarker        = $mixMarkerHit
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
