<#
.SYNOPSIS
    Convert the audit's to_replace.csv into sldl-compatible input.

.DESCRIPTION
    Input  : to_replace.csv (FR columns: Priorite;Tier;Dossier;Artiste;Titre;...)
    Output : sldl_input.csv (cols: Artist;Title;Album;Length;Dossier;Priorite;OrigFile)
             plus a JSON sidecar mapping each row to its origin folder for routing.
#>
param(
    [Parameter(Mandatory)][string]$InputCsv,
    [Parameter(Mandatory)][string]$OutputCsv,
    [string]$MapJson = ($OutputCsv -replace '\.csv$', '_map.json'),
    [int]$OnlyTier = 0
)

if (-not (Test-Path -LiteralPath $InputCsv)) { throw "Input CSV not found: $InputCsv" }

$rows = Import-Csv -LiteralPath $InputCsv -Delimiter ';' -Encoding UTF8

if ($OnlyTier -gt 0) {
    $rows = $rows | Where-Object { [int]$_.Priorite -eq $OnlyTier }
}

$rows = $rows | Sort-Object { [int]$_.Priorite }, Dossier, Titre

# Sanitize artist/title for sldl (strip label codes, normalize)
function Clean {
    param([string]$s)
    if (-not $s) { return '' }
    $s = $s -replace '\[[A-Za-z]+\d+\]', ''   # [MELCURE010]
    $s = $s -replace '\[\d+\]', ''            # [001]
    $s = $s.Trim()
    return $s
}

$out = New-Object System.Collections.Generic.List[object]
$map = @{}
$skippedNoArtist = 0

$i = 0
foreach ($r in $rows) {
    $i++
    $artist = Clean $r.Artiste
    $title = Clean $r.Titre
    if (-not $title) { continue }   # skip rows we can't search
    if (-not $artist) { $skippedNoArtist++; continue }   # too risky with strict matching

    $row = [pscustomobject]@{
        Artist   = $artist
        Title    = $title
        Album    = ''
        Length   = ''
        Dossier  = $r.Dossier
        Priorite = $r.Priorite
        OrigFile = $r.Fichier
    }
    $out.Add($row)

    # Map key = "Artist - Title" (normalized) so we can look up the destination
    # later when files come back from sldl.
    $key = ($artist + ' - ' + $title).ToLower()
    if (-not $map.ContainsKey($key)) {
        $map[$key] = @{
            dossier  = $r.Dossier
            origFile = $r.Fichier
            priorite = $r.Priorite
            tier     = $r.Tier
        }
    }
}

$out | Export-Csv -LiteralPath $OutputCsv -NoTypeInformation -Encoding UTF8
$map | ConvertTo-Json -Depth 4 | Out-File -LiteralPath $MapJson -Encoding UTF8

Write-Host "Wrote $($out.Count) rows to $OutputCsv"
Write-Host "Wrote routing map ($($map.Keys.Count) keys) to $MapJson"
if ($skippedNoArtist -gt 0) {
    Write-Host "Skipped $skippedNoArtist rows with no artist (too risky for sldl strict matching)"
}
