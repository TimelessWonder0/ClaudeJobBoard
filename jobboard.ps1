<#
.SYNOPSIS
  One-command job board run: Python fetches, filters and scores; you read output\jobs_latest.md or the Excel board.

.DESCRIPTION
  Runs `node scan.mjs --company <name>` for each provider the Python fetchers do not cover (Apple, Google, Pinpoint; skip
  with -SkipProviders), then `python -m board run`. Zero Claude calls, zero tokens.

    output\jobs_latest.md          the plain-text board Claude reads (and a dated copy, jobs_<date>.md)
    output\JobBoard_Master.xlsx    the board you edit; Status, Applied Date, Resume, Cover Letter and Notes survive by ID
    output\JobBoard_<date>.xlsx    this run's snapshot
    logs\<date>.log                everything the run printed
    logs\board_<date>.log          the full detail of the board run (boards, funnel, 25 sample drops)

  The console shows the summary the board prints (20 lines at most). Excel opens only with -Open.
  No scheduled task is created; you run it when you want.

.PARAMETER Open
  Open JobBoard_Master.xlsx in Excel when the run finishes.
.PARAMETER Only
  Fetch only these companies or ATS names (for testing), e.g. -Only Stripe,greenhouse
.PARAMETER Full
  Pull every posting from Workday, SmartRecruiters, Amazon, Eightfold and Oracle (no keyword queries). Slow; for testing.
.PARAMETER BoardTimeout
  Per-board time limit in seconds (default: fetch.board_time_limit_seconds in config\filters.yaml).
.PARAMETER SkipProviders
  Skip the node scan.mjs step that refreshes Apple, Google and Pinpoint (the providers the Python fetchers do not cover).
  That step runs by default and is slow; with this switch those three only show what the last scan already saw.
.PARAMETER LockWaitSeconds
  How long to wait for you to close JobBoard_Master.xlsx if it is open in Excel (default 300).

.NOTES
  Exit codes: 0 ok | 1 the run failed | 2 every board failed (check your internet) | 3 prerequisite missing
  Keep this file plain ASCII: Windows PowerShell 5.1 reads BOM-less scripts as ANSI.
#>
[CmdletBinding()]
param(
    [switch]$Open,
    [string[]]$Only,
    [switch]$Full,
    [double]$BoardTimeout = 0,
    [switch]$SkipProviders,
    [int]$LockWaitSeconds = 300
)

$ErrorActionPreference = 'Stop'
$script:Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$script:Today = Get-Date -Format 'yyyy-MM-dd'
$script:LogDir = Join-Path $script:Root 'logs'
$script:OutDir = Join-Path $script:Root 'output'
$script:LogFile = Join-Path $script:LogDir ($script:Today + '.log')
$script:Master = Join-Path $script:OutDir 'JobBoard_Master.xlsx'

# ------------------------------------------------------------------------------------------------
# logging (helpers are also used by tests\jobboard.tests.ps1, which dot-sources this file)
# ------------------------------------------------------------------------------------------------

function Write-Log {
    # Level LOG goes to the log file only; the console stays quiet so the board's own summary is all you see.
    param([string]$Message, [string]$Level = 'INFO')
    $stamp = Get-Date -Format 'HH:mm:ss'
    if (-not (Test-Path $script:LogDir)) { New-Item -ItemType Directory -Path $script:LogDir -Force | Out-Null }
    Add-Content -Path $script:LogFile -Value ("[{0}] {1,-5} {2}" -f $stamp, $Level, $Message) -Encoding UTF8
    switch ($Level) {
        'ERROR' { Write-Host $Message -ForegroundColor Red }
        'WARN'  { Write-Host $Message -ForegroundColor Yellow }
        'OK'    { Write-Host $Message -ForegroundColor Green }
        'LOG'   { }
        default { Write-Host $Message }
    }
}

function Add-LogRaw {
    param([string]$Text)
    Add-Content -Path $script:LogFile -Value $Text -Encoding UTF8
}

function New-StepFailure {
    param([string]$Step, [string]$Reason, [int]$ExitCode = 1)
    $ex = New-Object System.Exception("$Step failed: $Reason")
    $ex.Data['ExitCode'] = $ExitCode
    $ex.Data['Step'] = $Step
    $ex.Data['Reason'] = $Reason
    return $ex
}

function Invoke-Logged {
    # Run a native command, write its output to the log, return its exit code and lines. With -Echo the lines also go to the
    # console exactly as printed (no indent), which is how the board's summary reaches you.
    param([string]$Exe, [string[]]$Arguments, [string]$WorkDir, [switch]$Echo)
    $lines = New-Object System.Collections.ArrayList
    Push-Location $WorkDir
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'          # PowerShell 5.1 turns native stderr into errors; keep going
    try {
        & $Exe @Arguments 2>&1 | ForEach-Object {
            $s = if ($_ -is [System.Management.Automation.ErrorRecord]) { $_.ToString() } else { "$_" }
            [void]$lines.Add($s)
            if ($Echo) { Write-Host $s }
            Add-LogRaw ("    " + $s)
        }
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prev
        Pop-Location
    }
    return [pscustomobject]@{ ExitCode = $code; Lines = $lines.ToArray() }
}

function Test-FileLocked {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return $false }
    try {
        $fs = [System.IO.File]::Open($Path, 'Open', 'ReadWrite', 'None')
        $fs.Close()
        return $false
    } catch [System.IO.IOException] {
        return $true
    } catch [System.UnauthorizedAccessException] {
        return $true
    }
}

function Wait-MasterUnlocked {
    param([string]$Path, [int]$Seconds)
    if (-not (Test-FileLocked $Path)) { return $true }
    Write-Log ("JobBoard_Master.xlsx is open in Excel. Close it now; waiting up to {0} seconds..." -f $Seconds) 'WARN'
    $deadline = (Get-Date).AddSeconds($Seconds)
    $nextNag = (Get-Date).AddSeconds(15)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 2
        if (-not (Test-FileLocked $Path)) {
            Write-Log 'Master file is closed; continuing.' 'OK'
            return $true
        }
        if ((Get-Date) -ge $nextNag) {
            Write-Host '  ...still open. Close JobBoard_Master.xlsx in Excel (Save if asked).' -ForegroundColor Yellow
            $nextNag = (Get-Date).AddSeconds(15)
        }
    }
    Write-Log 'Master file is still open after waiting; the board will save a timestamped copy instead of updating it.' 'WARN'
    return $false
}

# ------------------------------------------------------------------------------------------------
# pure helpers (tested)
# ------------------------------------------------------------------------------------------------

function Get-BoardArguments {
    # The arguments for `python`. Only the switches you passed are added; nothing here can start a Claude call.
    param([string[]]$Only, [switch]$Full, [double]$BoardTimeout = 0)
    $result = @('-m', 'board', 'run')
    if ($Only -and $Only.Count -gt 0) { $result += '--only'; $result += @($Only | ForEach-Object { "$_" }) }
    if ($Full) { $result += '--full' }
    if ($BoardTimeout -gt 0) { $result += '--board-timeout'; $result += ([string]$BoardTimeout) }
    return $result
}

function Get-BoardExitCode {
    # python -m board run: 0 ok, 2 every board failed, 3 could not write the workbook. Anything else is a failed run.
    param([int]$PythonExit)
    switch ($PythonExit) {
        0 { return 0 }
        2 { return 2 }
        3 { return 3 }
        default { return 1 }
    }
}

function Get-ExcelPathFromOutput {
    # The board prints "Excel: <path>" (the master, or a timestamped copy when the master was open).
    param([string[]]$Lines, [string]$Default)
    foreach ($l in $Lines) {
        if ($l -match '^Excel:\s+(.+?)\s*$') { return $Matches[1] }
    }
    return $Default
}

function Get-FailureHint {
    param([int]$Code)
    switch ($Code) {
        2 { return 'Every board failed. Check your internet connection (and any VPN or proxy), then run jobboard again.' }
        3 { return 'The board could not write its workbook. Close JobBoard_Master.xlsx and run jobboard again.' }
        default { return 'The board run failed. The lines above and logs\board_<date>.log say why.' }
    }
}

# ------------------------------------------------------------------------------------------------
# steps
# ------------------------------------------------------------------------------------------------

function Get-ProviderScanArguments {
    # The scan.mjs arguments for one provider company. --company limits the scan to that company; --since is the window.
    param([string]$Company, [int]$Days)
    return @('scan.mjs', '--company', $Company, '--since', "$Days", '--json')
}

function Get-ProviderCompanies {
    # Companies whose board only career-ops can read (Apple, Google, Pinpoint ones), from config\companies.yaml.
    param([string]$WorkDir)
    $code = "from board.fetch.boards import load_boards; print(chr(10).join(b['name'] for b in load_boards() if b['ats'] == 'scanmjs'))"
    $res = Invoke-Logged -Exe 'python' -Arguments @('-c', $code) -WorkDir $WorkDir
    if ($res.ExitCode -ne 0) { return @() }
    return @($res.Lines | ForEach-Object { $_.Trim() } | Where-Object { $_ })
}

function Step-Providers {
    # scan.mjs exits 2 when ANY board failed and 1 on a fatal error. A failed provider is a warning: the Python run still goes ahead.
    $env:CAREER_OPS_ROOT = Join-Path $script:Root 'career-ops-data'
    $days = (& python -c "from board import config; print(config.filters_config()['days'])" 2>$null)
    if (-not $days) { $days = 4 }
    $companies = @(Get-ProviderCompanies -WorkDir $script:Root)
    if ($companies.Count -eq 0) { Write-Log 'No provider companies to scan with scan.mjs.' 'LOG'; return }
    Write-Host ("Refreshing {0} with scan.mjs (use -SkipProviders to skip)..." -f ($companies -join ', '))
    foreach ($c in $companies) {
        $res = Invoke-Logged -Exe 'node' -Arguments (Get-ProviderScanArguments -Company $c -Days ([int]$days)) -WorkDir (Join-Path $script:Root 'career-ops')
        if ($res.ExitCode -ne 0 -and $res.ExitCode -ne 2) {
            Write-Log ("scan.mjs for {0} exited with code {1}; it may be out of date." -f $c, $res.ExitCode) 'WARN'
        }
    }
}

function Step-Board {
    Write-Log 'python -m board run' 'LOG'
    [void](Wait-MasterUnlocked -Path $script:Master -Seconds $LockWaitSeconds)
    $env:CAREER_OPS_ROOT = Join-Path $script:Root 'career-ops-data'
    $env:PYTHONIOENCODING = 'utf-8'
    $arguments = Get-BoardArguments -Only $Only -Full:$Full -BoardTimeout $BoardTimeout
    return (Invoke-Logged -Exe 'python' -Arguments $arguments -WorkDir $script:Root -Echo)
}

function Step-Open {
    param([string]$Path)
    if (-not (Test-Path $Path)) {
        Write-Log ("Cannot open {0}: it does not exist." -f $Path) 'ERROR'
        return $false
    }
    try {
        Start-Process -FilePath $Path
        return $true
    } catch {
        Write-Log ("Could not open {0}: {1}" -f $Path, $_.Exception.Message) 'ERROR'
        return $false
    }
}

# ------------------------------------------------------------------------------------------------
# main
# ------------------------------------------------------------------------------------------------

function Main {
    New-Item -ItemType Directory -Path $script:LogDir -Force | Out-Null
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    Add-LogRaw ''
    Write-Log ("===== jobboard run {0}  (Open={1} Only={2} Full={3} SkipProviders={4}) =====" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Open, ($Only -join ','), $Full, $SkipProviders) 'LOG'

    if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
        Write-Log 'python is not on PATH. Install it and open a new terminal.' 'ERROR'
        return 3
    }
    if (-not $SkipProviders -and -not (Get-Command node -ErrorAction SilentlyContinue)) {
        Write-Log 'node is not on PATH, so the Apple / Google / Pinpoint step cannot run. Install Node, or run jobboard -SkipProviders.' 'ERROR'
        return 3
    }
    try {
        if (-not $SkipProviders) { Step-Providers }
        $res = Step-Board
    } catch {
        Write-Log ("Unexpected error at {0}: {1}" -f $_.InvocationInfo.PositionMessage.Trim(), $_.Exception.Message) 'ERROR'
        return 1
    }
    $code = Get-BoardExitCode -PythonExit $res.ExitCode
    if ($code -ne 0) {
        Write-Log (Get-FailureHint -Code $code) 'ERROR'
        return $code
    }
    if ($Open) {
        $target = Get-ExcelPathFromOutput -Lines $res.Lines -Default $script:Master
        if (-not (Step-Open -Path $target)) { return 1 }
    }
    Write-Log 'Done.' 'LOG'
    return 0
}

if ($MyInvocation.InvocationName -ne '.') {
    $code = @(Main)[-1]
    exit $code
}
