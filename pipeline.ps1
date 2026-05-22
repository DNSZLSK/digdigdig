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

.PARAMETER DoRetry
    Run Phase E : retry missed/fake tracks with query variants.

.PARAMETER DoDeploy
    Run Phase F : copy authentic FLACs to USB in correct folders. Off by default
    because it modifies the USB - opt-in explicitly.

.PARAMETER DeleteOld
    Together with -DoDeploy : also delete the old fake .wav files from the USB
    once their FLAC replacement is in place.

.PARAMETER UsbRoot
    Target USB root for deployment (default: D:\2023 Playlist Ultime)
#>
[CmdletBinding()]
param(
    [string]$InputCsv = "D:\2023 Playlist Ultime\to_replace.csv",
    [string]$UsbRoot = "D:\2023 Playlist Ultime",
    [int]$Limit = 0,
    [int]$OnlyTier = 0,
    [switch]$SkipConvert,
    [switch]$SkipDownload,
    [switch]$SkipVerify,
    [switch]$DoRetry,
    [switch]$DoDeploy,
    [switch]$DeleteOld
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

function Parse-SldlMisses {
    # Parse the sldl.log to find tracks reported as "Not found" or "All downloads failed"
    param([string]$SldlLog, [string]$SldlInputCsv, [string]$OutCsv)
    if (-not (Test-Path -LiteralPath $SldlLog))   { return 0 }
    if (-not (Test-Path -LiteralPath $SldlInputCsv)) { return 0 }
    $log = Get-Content -LiteralPath $SldlLog -Raw
    # Match patterns: "Not found: Artist - Title" and "All downloads failed: ..."
    $misses = New-Object System.Collections.Generic.HashSet[string]
    foreach ($m in [regex]::Matches($log, '(?im)^(?:Not found|All downloads failed):\s*(.+)$')) {
        [void]$misses.Add($m.Groups[1].Value.Trim())
    }
    $rows = Import-Csv -LiteralPath $SldlInputCsv -Encoding UTF8
    $hits = $rows | Where-Object {
        $key = "$($_.Artist) - $($_.Title)"
        $misses.Contains($key)
    }
    $hits | Export-Csv -LiteralPath $OutCsv -NoTypeInformation -Encoding UTF8
    return $hits.Count
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

# Step 4: parse misses
Log 'Step 4 : parse sldl misses'
$missCsv = "$Root\outputs\sldl_misses.csv"
$missCount = Parse-SldlMisses -SldlLog "$Root\logs\sldl.log" -SldlInputCsv "$Root\inputs\sldl_input.csv" -OutCsv $missCsv
Log ("Missed tracks : {0} -> {1}" -f $missCount, $missCsv)

# Step 5: retry misses (opt-in)
if ($DoRetry -and $missCount -gt 0) {
    Log 'Step 5 : retry missed tracks with query variants'
    $creds = if ($null -eq $creds) { Get-SoulseekCreds } else { $creds }
    # stop slskd if it crept back in
    Get-Process slskd -ErrorAction SilentlyContinue | Stop-Process -Force
    & "$Root\lib\retry-fakes.ps1" `
        -MissCsv $missCsv `
        -MapJson "$Root\inputs\sldl_input_map.json" `
        -SldlExe "$Root\bin\sldl\sldl.exe" `
        -SldlConfig "$Root\config\sldl.conf" `
        -SoulseekUser $creds.User `
        -SoulseekPass $creds.Pass `
        -StagingRetryDir "$Root\staging\retry"
    Log 'retry complete'
} else {
    Log 'Step 5 : SKIPPED (-DoRetry not set or no misses)'
}

# Step 6: deploy to USB (opt-in, destructive on USB)
if ($DoDeploy) {
    Log 'Step 6 : deploy authentic FLACs to USB'
    $latestReport = Get-ChildItem "$Root\staging" -Filter 'flac_report_*.txt' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    $reportPath = if ($latestReport) { $latestReport.FullName } else { '' }
    & "$Root\lib\route-files.ps1" `
        -StagingDir "$Root\staging" `
        -MapJson "$Root\inputs\sldl_input_map.json" `
        -UsbRoot $UsbRoot `
        -DetectiveReport $reportPath `
        -OutputDir "$Root\outputs" `
        -DeleteOld:$DeleteOld
    Log 'deploy complete'
} else {
    Log 'Step 6 : SKIPPED (-DoDeploy not set)'
}

# Final report
$staged = Get-ChildItem "$Root\staging" -Recurse -File -ErrorAction SilentlyContinue
Log ("Staging contains {0} files ({1:N1} MB total)" -f $staged.Count, (($staged | Measure-Object Length -Sum).Sum / 1MB))

Log ('===== PIPELINE END =====')
