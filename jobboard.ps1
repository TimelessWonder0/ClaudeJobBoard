<#
.SYNOPSIS
  One-command job board run: scan, build the Excel board, optionally review, summarize, open Excel.

.DESCRIPTION
  1. career-ops scan:   node scan.mjs --since <Days> --json   -> logs\<date>_scan.json
  2. board build:       python -m board run                   -> output\JobBoard_<date>.xlsx + JobBoard_Master.xlsx
  3. review (-Review):  claude -p "/review"                   -> "Why it fits" / "Gaps" for the top 25
  4. summary:           companies scanned, errored boards, roles found / kept / hidden, new since last run
  5. open:              output\JobBoard_Master.xlsx in Excel (skip with -NoOpen)

  Everything is logged to logs\<date>.log. No scheduled task is created; you run it when you want.

.PARAMETER Review
  Run claude -p "/review" after the board is built.
.PARAMETER NoOpen
  Do not open Excel at the end.
.PARAMETER Days
  Posting window for the scan (default 4).
.PARAMETER SkipScan
  Reuse state\last_scan.json instead of scanning (for re-running the board build only).
.PARAMETER LockWaitSeconds
  How long to wait for you to close JobBoard_Master.xlsx if it is open in Excel (default 300).

.NOTES
  Exit codes: 0 ok | 1 a step failed | 2 every board failed (check your internet) | 3 prerequisite missing
#>
[CmdletBinding()]
param(
    [switch]$Review,
    [switch]$NoOpen,
    [int]$Days = 4,
    [switch]$SkipScan,
    [int]$LockWaitSeconds = 300
)

$ErrorActionPreference = 'Stop'
$script:Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$script:Today = Get-Date -Format 'yyyy-MM-dd'
$script:LogDir = Join-Path $script:Root 'logs'
$script:StateDir = Join-Path $script:Root 'state'
$script:OutDir = Join-Path $script:Root 'output'
$script:LogFile = Join-Path $script:LogDir ($script:Today + '.log')
$script:Master = Join-Path $script:OutDir 'JobBoard_Master.xlsx'
$script:Failures = New-Object System.Collections.ArrayList

# ------------------------------------------------------------------------------------------------
# logging
# ------------------------------------------------------------------------------------------------

function Write-Log {
    param([string]$Message, [string]$Level = 'INFO')
    $stamp = Get-Date -Format 'HH:mm:ss'
    if (-not (Test-Path $script:LogDir)) { New-Item -ItemType Directory -Path $script:LogDir -Force | Out-Null }
    Add-Content -Path $script:LogFile -Value ("[{0}] {1,-5} {2}" -f $stamp, $Level, $Message) -Encoding UTF8
    switch ($Level) {
        'ERROR' { Write-Host $Message -ForegroundColor Red }
        'WARN'  { Write-Host $Message -ForegroundColor Yellow }
        'STEP'  { Write-Host $Message -ForegroundColor Cyan }
        'OK'    { Write-Host $Message -ForegroundColor Green }
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

# ------------------------------------------------------------------------------------------------
# helpers (also used by tests\jobboard.tests.ps1, which dot-sources this file)
# ------------------------------------------------------------------------------------------------

function Invoke-Logged {
    # Run a native command, stream its output to the console and the log, return its exit code and lines.
    param([string]$Exe, [string[]]$Arguments, [string]$WorkDir)
    $lines = New-Object System.Collections.ArrayList
    Push-Location $WorkDir
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'          # PowerShell 5.1 turns native stderr into errors; keep going
    try {
        & $Exe @Arguments 2>&1 | ForEach-Object {
            $s = if ($_ -is [System.Management.Automation.ErrorRecord]) { $_.ToString() } else { "$_" }
            [void]$lines.Add($s)
            Write-Host ("    " + $s)
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

function Read-Receipt {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return $null }
    try {
        $raw = Get-Content -Path $Path -Raw -Encoding UTF8
        if ([string]::IsNullOrWhiteSpace($raw)) { return $null }
        $obj = $raw | ConvertFrom-Json
        if ($null -eq $obj.version) { return $null }
        return $obj
    } catch {
        return $null
    }
}

function Test-AllBoardsFailed {
    # scan.mjs --json exits 2 when ANY board failed, so exit 2 alone does not mean "everything failed".
    param([int]$ExitCode, $Receipt)
    if ($ExitCode -ne 2) { return $false }
    if ($null -eq $Receipt) { return $true }
    $scanned = [int]$Receipt.scanned
    $errors = @($Receipt.errors | Where-Object { $null -ne $_ })
    if ($scanned -le 0) { return $true }
    # A board that answered with zero jobs and no successful request (unverified_zero) also counts as failed.
    $failed = @($errors | ForEach-Object { $_.company }) + @($Receipt.unverified_zero | Where-Object { $null -ne $_ })
    $failedCount = @($failed | Select-Object -Unique).Count
    if ($failedCount -ge $scanned) { return $true }
    return (([int]$Receipt.found -eq 0) -and ($errors.Count -gt 0))      # nothing was read at all
}

function Get-ErroredBoards {
    param($Receipt)
    if ($null -eq $Receipt) { return @() }
    $out = @()
    foreach ($e in @($Receipt.errors)) {
        if ($null -ne $e) { $out += ("{0} ({1})" -f $e.company, $e.error) }
    }
    return $out
}

function Get-PartialBoardWarnings {
    param([string]$ErrFile)
    if (-not (Test-Path $ErrFile)) { return @() }
    $hits = Select-String -Path $ErrFile -Pattern 'offset-clamped|truncated at|hit the .*-page cap|still incomplete' -ErrorAction SilentlyContinue
    return @($hits | ForEach-Object { $_.Line.Trim() })
}

function Show-ScanProgress {
    # Print the newest "scan: N/M sources done" line from the scan's stderr file while it runs.
    param([string]$ErrFile, [ref]$Last)
    try {
        $fs = [System.IO.File]::Open($ErrFile, 'Open', 'Read', 'ReadWrite')
        $sr = New-Object System.IO.StreamReader($fs)
        $text = $sr.ReadToEnd()
        $sr.Close()
    } catch { return }
    $m = [regex]::Matches($text, 'scan: (\d+/\d+) sources done')
    if ($m.Count -gt 0) {
        $now = $m[$m.Count - 1].Groups[1].Value
        if ($now -ne $Last.Value) {
            $Last.Value = $now
            Write-Host ("    scanned {0} sources" -f $now)
        }
    }
}

# ------------------------------------------------------------------------------------------------
# steps
# ------------------------------------------------------------------------------------------------

function Step-Scan {
    $receiptPath = Join-Path $script:LogDir ($script:Today + '_scan.json')
    $errPath = Join-Path $script:LogDir ($script:Today + '_scan.err')
    $stateReceipt = Join-Path $script:StateDir 'last_scan.json'
    New-Item -ItemType Directory -Path $script:StateDir -Force | Out-Null

    if ($SkipScan) {
        Write-Log 'Step 1/5  scan skipped (-SkipScan); using state\last_scan.json' 'STEP'
        return [pscustomobject]@{ Receipt = (Read-Receipt $stateReceipt); ErrFile = $errPath; Skipped = $true }
    }

    Write-Log ("Step 1/5  career-ops scan (node scan.mjs --since {0} --json), about 5 minutes" -f $Days) 'STEP'
    $env:CAREER_OPS_ROOT = Join-Path $script:Root 'career-ops-data'
    $started = Get-Date
    $proc = Start-Process -FilePath 'node' -ArgumentList @('scan.mjs', '--since', "$Days", '--json') `
        -WorkingDirectory (Join-Path $script:Root 'career-ops') -NoNewWindow -PassThru `
        -RedirectStandardOutput $receiptPath -RedirectStandardError $errPath
    $null = $proc.Handle          # cache the handle so ExitCode is readable after the process ends (PowerShell 5.1 quirk)
    $last = ''
    while (-not $proc.HasExited) {
        Start-Sleep -Seconds 5
        Show-ScanProgress -ErrFile $errPath -Last ([ref]$last)
    }
    $proc.WaitForExit()
    $code = $proc.ExitCode
    $secs = [int]((Get-Date) - $started).TotalSeconds
    Add-LogRaw '--- scan stderr ---'
    if (Test-Path $errPath) { Get-Content $errPath -Encoding UTF8 | ForEach-Object { Add-LogRaw ("    " + $_) } }
    Add-LogRaw '--- end scan stderr ---'
    Write-Log ("Scan finished in {0}s with exit code {1}" -f $secs, $code)

    $receipt = Read-Receipt $receiptPath
    $script:LastScan = [pscustomobject]@{ Receipt = $receipt; ErrFile = $errPath; Skipped = $false }
    if (Test-AllBoardsFailed -ExitCode $code -Receipt $receipt) {
        throw (New-StepFailure 'Step 1 (scan)' 'every board failed. Check your internet connection (and any VPN or proxy), then run jobboard again.' 2)
    }
    if ($code -eq 1 -or ($code -ne 0 -and $code -ne 2)) {
        $tail = ''
        if (Test-Path $errPath) { $tail = (Get-Content $errPath -Tail 8 -Encoding UTF8) -join ' | ' }
        throw (New-StepFailure 'Step 1 (scan)' ("scan.mjs exited with code {0}. Last output: {1}" -f $code, $tail) 1)
    }
    if ($null -eq $receipt) {
        throw (New-StepFailure 'Step 1 (scan)' ("the scan finished (exit {0}) but wrote no readable receipt to {1}" -f $code, $receiptPath) 1)
    }
    Copy-Item -Path $receiptPath -Destination $stateReceipt -Force
    $errored = Get-ErroredBoards $receipt
    if ($errored.Count -gt 0) {
        Write-Log ("{0} board(s) errored; the rest were read:" -f $errored.Count) 'WARN'
        $errored | ForEach-Object { Write-Log ("  - " + $_) 'WARN' }
    }
    Write-Log ("Receipt saved to {0}" -f $receiptPath) 'OK'
    return [pscustomobject]@{ Receipt = $receipt; ErrFile = $errPath; Skipped = $false }
}

function Step-Board {
    Write-Log 'Step 2/5  build the board (python -m board run)' 'STEP'
    $lastRun = Join-Path $script:StateDir 'last_run.json'
    if (Test-Path $lastRun) { Remove-Item $lastRun -Force }
    [void](Wait-MasterUnlocked -Path $script:Master -Seconds $LockWaitSeconds)
    $env:CAREER_OPS_ROOT = Join-Path $script:Root 'career-ops-data'
    $env:PYTHONIOENCODING = 'utf-8'
    $res = Invoke-Logged -Exe 'python' -Arguments @('-m', 'board', 'run') -WorkDir $script:Root
    if ($res.ExitCode -ne 0) {
        $msg = @($res.Lines | Where-Object { $_ -match '^error:' } | Select-Object -First 1)
        $why = if ($msg.Count -gt 0) { $msg[0] } else { ("python -m board run exited with code {0}; see the log above" -f $res.ExitCode) }
        throw (New-StepFailure 'Step 2 (board run)' $why 1)
    }
    if (-not (Test-Path $lastRun)) {
        throw (New-StepFailure 'Step 2 (board run)' 'it finished but wrote no state\last_run.json summary' 1)
    }
    Write-Log 'Board built.' 'OK'
    return (Get-Content $lastRun -Raw -Encoding UTF8 | ConvertFrom-Json)
}

function Step-Review {
    Write-Log 'Step 3/5  review the top 25 (claude -p "/review")' 'STEP'
    if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
        throw (New-StepFailure 'Step 3 (review)' 'the claude command is not on PATH. Install Claude Code, or run /review inside Claude Code.' 1)
    }
    [void](Wait-MasterUnlocked -Path $script:Master -Seconds $LockWaitSeconds)
    $res = Invoke-Logged -Exe 'claude' -WorkDir $script:Root -Arguments @(
        '-p', '/review', '--allowedTools', 'Bash(python -m board review:*)', 'Read', 'Write')
    if ($res.ExitCode -ne 0) {
        $tail = ($res.Lines | Select-Object -Last 3) -join ' | '
        throw (New-StepFailure 'Step 3 (review)' ("claude exited with code {0}. {1}" -f $res.ExitCode, $tail) 1)
    }
    Write-Log 'Review finished.' 'OK'
}

function Show-Summary {
    param($ScanResult, $Run)
    Write-Host ''
    Write-Log '================ SUMMARY ================' 'STEP'
    $r = if ($null -ne $ScanResult) { $ScanResult.Receipt } else { $null }
    if ($null -ne $r) {
        $note = if ($ScanResult.Skipped) { ' (from the previous scan)' } else { '' }
        Write-Log ("Companies scanned: {0}{1}   jobs seen: {2}   new postings added by scan: {3}" -f $r.scanned, $note, $r.found, $r.added)
        $errored = Get-ErroredBoards $r
        if ($errored.Count -eq 0) { Write-Log 'Boards that errored: none' }
        else {
            Write-Log ("Boards that errored: {0}" -f $errored.Count) 'WARN'
            $errored | ForEach-Object { Write-Log ("  - " + $_) 'WARN' }
        }
        $partial = Get-PartialBoardWarnings $ScanResult.ErrFile
        if ($partial.Count -gt 0) {
            Write-Log ("Boards only partly read: {0} (see the log)" -f $partial.Count) 'WARN'
            $partial | Select-Object -First 6 | ForEach-Object { Write-Log ("  - " + $_) 'WARN' }
        }
    } else {
        Write-Log 'Companies scanned: n/a (no scan receipt)' 'WARN'
    }
    if ($null -ne $Run) {
        Write-Log ("Roles found: {0}   kept: {1}   hidden: {2}   new since last run: {3}" -f $Run.roles_found, $Run.kept, $Run.hidden, $Run.new)
        Write-Log ("Board now holds {0} roles. Master: {1}" -f $Run.board_total, $Run.master)
        $rules = $Run.hidden_by_rule.PSObject.Properties | ForEach-Object { "{0}: {1}" -f $_.Name, $_.Value }
        if ($rules) { Write-Log ("Hidden by rule: " + ($rules -join ', ')) }
        foreach ($n in @($Run.notes)) { if ($n) { Write-Log ("NOTE: " + $n) 'WARN' } }
    } else {
        Write-Log 'Roles found / kept / hidden: n/a (the board step did not finish)' 'WARN'
    }
    if ($script:Failures.Count -gt 0) {
        Write-Log 'Steps that failed:' 'ERROR'
        $script:Failures | ForEach-Object { Write-Log ("  - " + $_) 'ERROR' }
    }
    Write-Log ("Log: {0}" -f $script:LogFile)
}

function Step-Open {
    param($Run)
    $target = $script:Master
    if ($null -ne $Run -and $Run.master -and (Test-Path $Run.master)) { $target = $Run.master }
    if ($NoOpen) { Write-Log 'Step 5/5  not opening Excel (-NoOpen)' 'STEP'; return }
    Write-Log 'Step 5/5  open the master workbook' 'STEP'
    if (-not (Test-Path $target)) {
        throw (New-StepFailure 'Step 5 (open Excel)' ("{0} does not exist" -f $target) 1)
    }
    try {
        Start-Process -FilePath $target
        Write-Log ("Opened {0}" -f $target) 'OK'
    } catch {
        throw (New-StepFailure 'Step 5 (open Excel)' ("could not open {0}: {1}" -f $target, $_.Exception.Message) 1)
    }
}

# ------------------------------------------------------------------------------------------------
# main
# ------------------------------------------------------------------------------------------------

function Main {
    New-Item -ItemType Directory -Path $script:LogDir -Force | Out-Null
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    Add-LogRaw ''
    Write-Log ("===== jobboard run {0}  (Review={1} NoOpen={2} Days={3} SkipScan={4}) =====" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Review, $NoOpen, $Days, $SkipScan)

    foreach ($tool in @('node', 'python')) {
        if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
            Write-Log ("{0} is not on PATH. Install it and open a new terminal." -f $tool) 'ERROR'
            return 3
        }
    }
    if (-not (Test-Path (Join-Path $script:Root 'career-ops\scan.mjs'))) {
        Write-Log 'career-ops\scan.mjs not found. Clone and install career-ops first (see README.md).' 'ERROR'
        return 3
    }

    $scan = $null
    $run = $null
    try {
        try {
            $scan = Step-Scan
        } catch {
            if ($_.Exception.Data['ExitCode']) {
                $script:Failures.Add($_.Exception.Message) | Out-Null
                Write-Log $_.Exception.Message 'ERROR'
                Show-Summary -ScanResult $script:LastScan -Run $null
                return [int]$_.Exception.Data['ExitCode']       # 2 = every board failed, 1 = scan error: stop before step 2
            }
            throw
        }
        try {
            $run = Step-Board
        } catch {
            if ($_.Exception.Data['ExitCode']) {
                $script:Failures.Add($_.Exception.Message) | Out-Null
                Write-Log $_.Exception.Message 'ERROR'
                Show-Summary -ScanResult $scan -Run $null
                return 1
            }
            throw
        }
        if ($Review) {
            try { Step-Review } catch {
                if ($_.Exception.Data['ExitCode']) { $script:Failures.Add($_.Exception.Message) | Out-Null; Write-Log $_.Exception.Message 'ERROR' } else { throw }
            }
        } else {
            Write-Log 'Step 3/5  review skipped (add -Review to run it, or use /review in Claude Code)' 'STEP'
        }
        Write-Log 'Step 4/5  summary' 'STEP'
        Show-Summary -ScanResult $scan -Run $run
        try { Step-Open -Run $run } catch {
            if ($_.Exception.Data['ExitCode']) { $script:Failures.Add($_.Exception.Message) | Out-Null; Write-Log $_.Exception.Message 'ERROR' } else { throw }
        }
    } catch {
        $msg = "Unexpected error at {0}: {1}" -f $_.InvocationInfo.PositionMessage.Trim(), $_.Exception.Message
        Write-Log $msg 'ERROR'
        return 1
    }
    if ($script:Failures.Count -gt 0) { return 1 }
    Write-Log 'Done.' 'OK'
    return 0
}

if ($MyInvocation.InvocationName -ne '.') {
    $code = @(Main)[-1]
    exit $code
}
