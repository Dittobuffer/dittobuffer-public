[CmdletBinding()]
param(
    [string]$Python = '',
    [switch]$Native,
    [string]$Cargo = 'cargo',
    [string]$NativeTarget = '',
    [string[]]$PytestArgs = @()
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
if (!$Python) {
    $localPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
    $Python = if (Test-Path -LiteralPath $localPython) { $localPython } else { 'python' }
}
$pythonCommand = (Get-Command $Python -ErrorAction Stop).Source
$nodeCommand = (Get-Command node -ErrorAction Stop).Source
if ($Native) { $cargoCommand = (Get-Command $Cargo -ErrorAction Stop).Source }

$runId = (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [Guid]::NewGuid().ToString('N').Substring(0,8)
$runDirectory = Join-Path $projectRoot ('.private\test-runs\' + $runId)
New-Item -ItemType Directory -Path $runDirectory -Force | Out-Null
$results = [System.Collections.Generic.List[object]]::new()

function Invoke-RegressionStep([string]$Name, [string]$Command, [string[]]$Arguments) {
    $log = Join-Path $runDirectory ($Name + '.log')
    Write-Host ('Running ' + $Name)
    $started = Get-Date
    $priorPreference = $ErrorActionPreference
    try {
        # Native stderr also carries ordinary compiler/pytest diagnostics on PS5.
        $ErrorActionPreference = 'Continue'
        & $Command @Arguments 2>&1 | Tee-Object -FilePath $log
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $priorPreference }
    $results.Add([PSCustomObject]@{name=$Name;exit_code=$code;seconds=((Get-Date)-$started).TotalSeconds;log=$log})
    if ($code -ne 0) { throw ($Name + ' failed; inspect ' + $log) }
}

$savedEnvironment = @{}
foreach ($name in @('PYTHONPATH','PYTHONDONTWRITEBYTECODE','CARGO_TARGET_DIR')) {
    $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
Push-Location $projectRoot
try {
    $env:PYTHONPATH = (Join-Path $projectRoot 'src') + [IO.Path]::PathSeparator + $projectRoot + $(if ($env:PYTHONPATH) { [IO.Path]::PathSeparator + $env:PYTHONPATH } else { '' })
    $env:PYTHONDONTWRITEBYTECODE = '1'
    Invoke-RegressionStep 'pytest' $pythonCommand (@('-m','pytest','-q','--basetemp',(Join-Path $runDirectory 'pytest-temp'),'-o',('cache_dir=' + (Join-Path $runDirectory 'pytest-cache'))) + $PytestArgs)
    foreach ($suite in @('ui','raids','family','world')) {
        Invoke-RegressionStep ('client-' + $suite) $nodeCommand @('scripts/test-client-' + $suite + '.js')
    }
    if ($Native) {
        $env:CARGO_TARGET_DIR = Join-Path $runDirectory 'native-target'
        $cargoArguments = @('test','--locked','--manifest-path','client/native/Cargo.toml','--lib')
        if ($NativeTarget) { $cargoArguments += @('--target',$NativeTarget) }
        Invoke-RegressionStep 'native-cargo' $cargoCommand $cargoArguments
    } else { Write-Host 'Native cargo tests: NOT_TESTED (enable with -Native).' }
    Write-Host ('All requested checks passed. Logs: ' + $runDirectory)
} finally {
    [PSCustomObject]@{native_requested=[bool]$Native;steps=@($results.ToArray())} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $runDirectory 'results.json') -Encoding UTF8
    foreach ($name in $savedEnvironment.Keys) { [Environment]::SetEnvironmentVariable($name,$savedEnvironment[$name],'Process') }
    Pop-Location
}
