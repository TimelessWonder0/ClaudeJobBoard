# Unit tests for the helper functions in jobboard.ps1. Run: powershell -NoProfile -File tests\jobboard.tests.ps1
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $here '..\jobboard.ps1') -NoOpen        # dot-sourcing defines the functions and does not run Main

$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ('jobboard_tests_' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tmp | Out-Null
$script:LogDir = $tmp
$script:LogFile = Join-Path $tmp 'test.log'

$script:failed = 0
function Assert-That {
    param([string]$Name, [bool]$Condition)
    if ($Condition) { Write-Host ("  ok    " + $Name) } else { Write-Host ("  FAIL  " + $Name) -ForegroundColor Red; $script:failed++ }
}
function New-Receipt {
    param([int]$Scanned, [int]$Found, [string[]]$ErrorCompanies = @(), [string[]]$UnverifiedZero = @())
    $errs = @($ErrorCompanies | ForEach-Object { [pscustomobject]@{ company = $_; error = 'fetch failed' } })
    return [pscustomobject]@{ version = 'careerops.scan.receipt@1'; scanned = $Scanned; found = $Found; added = 0; errors = $errs; unverified_zero = $UnverifiedZero }
}

try {
    Write-Host 'Test-AllBoardsFailed'
    $names = 1..49 | ForEach-Object { "Co$_" }
    Assert-That 'exit 0 is never "all failed"' (-not (Test-AllBoardsFailed -ExitCode 0 -Receipt (New-Receipt 49 100)))
    Assert-That 'exit 2 with no readable receipt is all failed' (Test-AllBoardsFailed -ExitCode 2 -Receipt $null)
    Assert-That 'exit 2, every board errored' (Test-AllBoardsFailed -ExitCode 2 -Receipt (New-Receipt 49 0 $names))
    Assert-That 'exit 2, 3 of 49 errored and jobs found: partial, continue' (-not (Test-AllBoardsFailed -ExitCode 2 -Receipt (New-Receipt 49 500 $names[0..2])))
    Assert-That 'exit 2, 48 errored + 1 unverified zero is all failed' (Test-AllBoardsFailed -ExitCode 2 -Receipt (New-Receipt 49 0 $names[0..47] @('Co49')))
    Assert-That 'exit 2, nothing found and errors present is all failed' (Test-AllBoardsFailed -ExitCode 2 -Receipt (New-Receipt 49 0 $names[0..4]))
    Assert-That 'exit 1 is a fatal error, not "all failed"' (-not (Test-AllBoardsFailed -ExitCode 1 -Receipt (New-Receipt 49 0 $names)))

    Write-Host 'Read-Receipt / Get-ErroredBoards'
    $good = Join-Path $tmp 'good.json'; (New-Receipt 5 10 @('Foo')) | ConvertTo-Json -Depth 5 | Set-Content $good -Encoding UTF8
    $bad = Join-Path $tmp 'bad.json'; 'not json' | Set-Content $bad
    $empty = Join-Path $tmp 'empty.json'; '' | Set-Content $empty
    Assert-That 'valid receipt parses' ($null -ne (Read-Receipt $good))
    Assert-That 'invalid receipt gives null' ($null -eq (Read-Receipt $bad))
    Assert-That 'empty receipt gives null' ($null -eq (Read-Receipt $empty))
    Assert-That 'missing receipt gives null' ($null -eq (Read-Receipt (Join-Path $tmp 'nope.json')))
    $errored = @(Get-ErroredBoards (Read-Receipt $good))
    Assert-That 'errored boards are listed with the message' ($errored.Count -eq 1 -and $errored[0] -eq 'Foo (fetch failed)')
    Assert-That 'no receipt means no errored boards' (@(Get-ErroredBoards $null).Count -eq 0)

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

    Write-Host 'Get-PartialBoardWarnings'
    $err = Join-Path $tmp 'scan.err'
    "ok line`n[x] workday: NVIDIA offset-clamped at 2000 - recovered 558 jobs (still incomplete)`n eightfold: Microsoft truncated at max_pages=200`nfine" | Set-Content $err
    Assert-That 'capped and truncated boards are reported' (@(Get-PartialBoardWarnings $err).Count -eq 2)

    Write-Host 'Invoke-Logged'
    $r = Invoke-Logged -Exe 'cmd.exe' -Arguments @('/c', 'echo hello & exit /b 3') -WorkDir $tmp
    Assert-That 'native exit code is returned' ($r.ExitCode -eq 3)
    $trimmed = @($r.Lines | ForEach-Object { $_.Trim() })
    Assert-That 'native output is captured and logged' (($trimmed -contains 'hello') -and ((Get-Content $script:LogFile -Raw) -match 'hello'))
} finally {
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}

if ($script:failed -gt 0) { Write-Host ("{0} FAILED" -f $script:failed) -ForegroundColor Red; exit 1 }
Write-Host 'ALL PASSED'
exit 0
