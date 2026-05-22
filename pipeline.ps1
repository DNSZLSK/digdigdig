<#
.SYNOPSIS
    searchseek - end-to-end pipeline : convert CSV -> sldl -> FLAC_Detective -> route.

.DESCRIPTION
    Phases:
      1. Convert to_replace.csv -> sldl_input.csv
      2. Run sldl with lossless profile, output to staging\
      3. Run flac-detective on staging\, output JSON verdict
      4. Split into authentic.csv / fake.csv based on verdict
      5. (later) retry fakes, then deploy authentic to USB

.PARAMETER InputCsv
    Path to the audit's to_replace.csv (default: D:\2023 Playlist Ultime\to_replace.csv)

.PARAMETER Limit
    Max number of tracks to process (for smoke testing). 0 = no limit.

.PARAMETER OnlyTier
    Restrict to one priority tier (1, 2, or 3). 0 = all tiers.

.PARAMETER SkipConvert
    Skip the CSV conversion step (use existing inputs\sldl_input.csv).

.PARAMETER SkipDownload
    Skip the sldl download step (just verify existing staging files).

.PARAMETER SkipVerify
    Skip flac-detective verification.
#>
[CmdletBinding()]
param(
    [string]$InputCsv = "D:\2023 Playlist Ultime\to_replace.csv",
    [int]$Limit = 0,
    [int]$OnlyTier = 0,
    [switch]$SkipConvert,
    [switch]$SkipDownload,
    [switch]$SkipVerify
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path $PSCommandPath -Parent
Set-Location $Root

# ----- helpers --------------------------------------------------------------

function Log {
    param([string]$Msg, [string]$Level = 'INFO')
    $ts = (Get-Date).ToString('HH:mm:ss')
    $line = "[$ts] [$Level] $Msg"
    Write-Host $line
    Add-Content -LiteralPath "$Root\logs\pipeline.log" -Value "[$((Get-Date).ToString('yyyy-MM-dd HH:mm:ss'))] [$Level] $Msg" -Encoding UTF8
}

function Get-SoulseekCreds {
    # Read from local slskd config to avoid duplicating creds in this project
    $slskdYml = "$env:LOCALAPPDATA\slskd\slskd.yml"
    if (-not (Test-Path -LiteralPath $slskdYml)) {
        throw "Cannot find slskd config at $slskdYml - Soulseek creds unavailable"
    }
    $content = Get-Content -LiteralPath $slskdYml -Raw
    $user = if ($content -match '(?ms)^soulseek:\s*\n\s*username:\s*(\S+)') { $Matches[1] } else { $null }
    $pass = if ($content -match '(?ms)^soulseek:\s*\n\s*username:[^\n]+\n\s*password:\s*(\S+)') { $Matches[1] } else { $null }
    if (-not $user -or -not $pass) {
        throw "Could not parse Soulseek user/pass from $slskdYml"
    }
    return @{ User = $user; Pass = $pass }
}

# ----- main -----------------------------------------------------------------

Log ('===== PIPELINE START =====')
Log ("Input  : {0}" -f $InputCsv)
Log ("Limit  : {0}" -f $Limit)
Log ("Tier   : {0}" -f $OnlyTier)

# Step 1: convert CSV
if (-not $SkipConvert) {
    Log 'Step 1/4 : convert CSV -> sldl input'
    & "$Root\lib\convert-csv.ps1" `
        -InputCsv $InputCsv `
        -OutputCsv "$Root\inputs\sldl_input.csv" `
        -OnlyTier $OnlyTier
} else {
    Log 'Step 1/4 : SKIPPED (using existing inputs\sldl_input.csv)'
}

# Step 2: sldl download
if (-not $SkipDownload) {
    Log 'Step 2/4 : sldl download to staging'

    # Verify slskd is stopped (Soulseek single-login)
    $slskd = Get-Process slskd -ErrorAction SilentlyContinue
    if ($slskd) {
        Log 'slskd is running - stopping it (Soulseek allows only one login per account)' 'WARN'
        $slskd | Stop-Process -Force
        Start-Sleep -Seconds 2
    }

    $creds = Get-SoulseekCreds
    Log ("Logging in as : {0}" -f $creds.User)

    $sldlArgs = @(
        "$Root\inputs\sldl_input.csv"
        '--input-type', 'csv'
        '--user', $creds.User
        '--pass', $creds.Pass
        '--config', "$Root\config\sldl.conf"
        '--profile', 'lossless'
        '--path', "$Root\staging"
    )
    if ($Limit -gt 0) {
        $sldlArgs += '-n'
        $sldlArgs += $Limit.ToString()
    }

    Log ("Calling : sldl.exe " + ($sldlArgs -join ' ').Replace($creds.Pass, '***'))
    & "$Root\bin\sldl\sldl.exe" @sldlArgs 2>&1 | Tee-Object -FilePath "$Root\logs\sldl.log" -Append | ForEach-Object {
        Write-Host $_
    }
    Log ('sldl exit code : {0}' -f $LASTEXITCODE)
} else {
    Log 'Step 2/4 : SKIPPED (using existing staging files)'
}

# Step 3: flac-detective verify
if (-not $SkipVerify) {
    Log 'Step 3/4 : flac-detective verify on staging'
    $env:PYTHONIOENCODING = 'utf-8'

    $flacFiles = Get-ChildItem "$Root\staging" -Recurse -File -Filter '*.flac' -ErrorAction SilentlyContinue
    if ($flacFiles.Count -eq 0) {
        Log 'No FLAC files in staging - skipping verify' 'WARN'
    } else {
        Log ("{0} FLAC files to verify" -f $flacFiles.Count)
        & "$Root\.venv\Scripts\flac-detective.exe" "$Root\staging" 2>&1 | Tee-Object -FilePath "$Root\logs\detective.log" -Append | Out-Host
        Log ('detective exit code : {0}' -f $LASTEXITCODE)
    }
} else {
    Log 'Step 3/4 : SKIPPED'
}

# Step 4: report (placeholder - full routing logic in lib\route-files.ps1 later)
Log 'Step 4/4 : report (TODO)'
$staged = Get-ChildItem "$Root\staging" -Recurse -File -ErrorAction SilentlyContinue
Log ("Staging contains {0} files ({1:N1} MB total)" -f $staged.Count, (($staged | Measure-Object Length -Sum).Sum / 1MB))

Log ('===== PIPELINE END =====')
