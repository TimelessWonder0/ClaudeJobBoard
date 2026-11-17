# Unit tests for the helper functions in jobboard.ps1. Run: powershell -NoProfile -File tests\jobboard.tests.ps1
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $here '..\jobboard.ps1')        # dot-sourcing defines the functions and does not run Main

$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ('jobboard_tests_' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tmp | Out-Null
$script:LogDir = $tmp
$script:LogFile = Join-Path $tmp 'test.log'

$script:failed = 0
function Assert-That {
    param([string]$Name, [bool]$Condition)
    if ($Condition) { Write-Host ("  ok    " + $Name) } else { Write-Host ("  FAIL  " + $Name) -ForegroundColor Red; $script:failed++ }
}

try {
    Write-Host 'Get-BoardArguments'
    Assert-That 'default is exactly python -m board run' ((@(Get-BoardArguments) -join ' ') -eq '-m board run')
    Assert-That '-Only adds the names after --only' ((@(Get-BoardArguments -Only @('Stripe', 'greenhouse')) -join ' ') -eq '-m board run --only Stripe greenhouse')
    Assert-That '-Full adds --full' ((@(Get-BoardArguments -Full) -join ' ') -eq '-m board run --full')
    Assert-That '-BoardTimeout adds the limit' ((@(Get-BoardArguments -BoardTimeout 120) -join ' ') -eq '-m board run --board-timeout 120')
    Assert-That 'a zero timeout is left to the config' ((@(Get-BoardArguments -BoardTimeout 0) -join ' ') -eq '-m board run')
    $all = (@(Get-BoardArguments -Only @('x') -Full -BoardTimeout 30) -join ' ')
    Assert-That 'no combination ever asks for Claude' (($all -notmatch 'claude') -and ($all -notmatch 'with-claude'))

    Write-Host 'Get-ProviderScanArguments / Get-ProviderCompanies'
    Assert-That 'one scan.mjs run per provider, limited with --company' ((@(Get-ProviderScanArguments -Company 'Apple' -Days 4) -join ' ') -eq 'scan.mjs --company Apple --since 4 --json')
    Assert-That 'the window comes from the caller, not a constant' ((@(Get-ProviderScanArguments -Company 'CFRA' -Days 7) -join ' ') -eq 'scan.mjs --company CFRA --since 7 --json')
    $providers = @(Get-ProviderCompanies -WorkDir (Join-Path $here '..'))
    Assert-That 'the provider companies come from companies.yaml (Apple, Google, CFRA)' (($providers -contains 'Apple') -and ($providers -contains 'Google') -and ($providers -contains 'CFRA'))
    Assert-That 'a Greenhouse company is not a provider' ($providers -notcontains 'Stripe')

    Write-Host 'Get-BoardExitCode / Get-FailureHint'
    Assert-That 'python 0 is ok' ((Get-BoardExitCode -PythonExit 0) -eq 0)
    Assert-That 'python 2 is every board failed' ((Get-BoardExitCode -PythonExit 2) -eq 2)
    Assert-That 'python 3 is could not write the workbook' ((Get-BoardExitCode -PythonExit 3) -eq 3)
    Assert-That 'python 1 and anything else is a failed run' (((Get-BoardExitCode -PythonExit 1) -eq 1) -and ((Get-BoardExitCode -PythonExit 255) -eq 1))
    Assert-That 'each failure code has a hint' (((Get-FailureHint -Code 2) -match 'internet') -and ((Get-FailureHint -Code 3) -match 'Close JobBoard_Master') -and ((Get-FailureHint -Code 1) -match 'failed'))

    Write-Host 'Get-ExcelPathFromOutput'
    $lines = @('Job board 2026-11-16  window 4d', 'Board: output\jobs_latest.md', 'Excel: C:\x\output\JobBoard_Master.xlsx', 'Detail: logs\b.log')
    Assert-That 'the Excel line is read from the board summary' ((Get-ExcelPathFromOutput -Lines $lines -Default 'D') -eq 'C:\x\output\JobBoard_Master.xlsx')
    Assert-That 'a timestamped copy is found too' ((Get-ExcelPathFromOutput -Lines @('Excel: C:\x\JobBoard_Master_20261116_0900.xlsx') -Default 'D') -eq 'C:\x\JobBoard_Master_20261116_0900.xlsx')
    Assert-That 'no Excel line falls back to the default' ((Get-ExcelPathFromOutput -Lines @('nothing') -Default 'D') -eq 'D')

    Write-Host 'Write-Log'
    Write-Log 'quiet line' 'LOG'
    Write-Log 'loud line' 'INFO'
    $logText = Get-Content $script:LogFile -Raw
    Assert-That 'both levels reach the log file' (($logText -match 'quiet line') -and ($logText -match 'loud line'))
    $console = & { Write-Log 'only for the file' 'LOG' } 6>&1 | Out-String
    Assert-That 'level LOG prints nothing to the console' ($console.Trim().Length -eq 0)

    Write-Host 'Test-FileLocked / Wait-MasterUnlocked'
    $xlsx = Join-Path $tmp 'JobBoard_Master.xlsx'; 'x' | Set-Content $xlsx
    Assert-That 'closed file is not locked' (-not (Test-FileLocked $xlsx))
    Assert-That 'missing file is not locked' (-not (Test-FileLocked (Join-Path $tmp 'missing.xlsx')))
    $fs = [System.IO.File]::Open($xlsx, 'Open', 'ReadWrite', 'None')
    try {
        Assert-That 'file held open by another handle is locked' (Test-FileLocked $xlsx)
        $t0 = Get-Date
        $ok = Wait-MasterUnlocked -Path $xlsx -Seconds 3
        Assert-That 'waiting on a held file times out and returns false' ((-not $ok) -and (((Get-Date) - $t0).TotalSeconds -ge 2.5))
    } finally { $fs.Close() }
    Assert-That 'released file returns true at once' (Wait-MasterUnlocked -Path $xlsx -Seconds 3)
    Assert-That 'the lock warning was logged' ((Get-Content $script:LogFile -Raw) -match 'open in Excel')

    Write-Host 'Invoke-Logged'
    $r = Invoke-Logged -Exe 'cmd.exe' -Arguments @('/c', 'echo hello & exit /b 3') -WorkDir $tmp
    Assert-That 'native exit code is returned' ($r.ExitCode -eq 3)
    $trimmed = @($r.Lines | ForEach-Object { $_.Trim() })
    Assert-That 'native output is captured and logged' (($trimmed -contains 'hello') -and ((Get-Content $script:LogFile -Raw) -match 'hello'))
    $echoed = & { Invoke-Logged -Exe 'cmd.exe' -Arguments @('/c', 'echo shown') -WorkDir $tmp -Echo | Out-Null } 6>&1 | Out-String
    Assert-That '-Echo prints the line unindented' ($echoed -match '(?m)^shown\s*$')
    $quiet = & { Invoke-Logged -Exe 'cmd.exe' -Arguments @('/c', 'echo hidden') -WorkDir $tmp | Out-Null } 6>&1 | Out-String
    Assert-That 'without -Echo the console stays quiet' ($quiet.Trim().Length -eq 0)

    Write-Host 'the script itself'
    $src = Get-Content (Join-Path $here '..\jobboard.ps1') -Raw
    Assert-That 'jobboard.ps1 never starts claude' (($src -notmatch "Get-Command claude") -and ($src -notmatch "Exe 'claude'") -and ($src -notmatch '-p\s+/review'))
    Assert-That 'Excel opens only with -Open (no -NoOpen switch is left)' (($src -match '\[switch\]\$Open') -and ($src -notmatch 'NoOpen'))
    Assert-That 'the providers step runs unless -SkipProviders is given (no -ScanProviders switch is left)' (($src -match '\[switch\]\$SkipProviders') -and ($src -match 'if \(-not \$SkipProviders\) \{ Step-Providers \}') -and ($src -notmatch 'ScanProviders'))
} finally {
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}

if ($script:failed -gt 0) { Write-Host ("{0} FAILED" -f $script:failed) -ForegroundColor Red; exit 1 }
Write-Host 'ALL PASSED'
exit 0
