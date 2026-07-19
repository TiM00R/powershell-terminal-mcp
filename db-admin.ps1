# db-admin.ps1 - thin wrapper around db_admin.py using the project venv python.
# Plain ASCII. Destructive commands default to a dry run; add --yes to execute.
#
# Examples:
#   .\db-admin.ps1 list
#   .\db-admin.ps1 clean-stale
#   .\db-admin.ps1 clean-stale --yes
#   .\db-admin.ps1 delete-ids 3 4 5 --yes
#   .\db-admin.ps1 prune --days 30
#   .\db-admin.ps1 prune --days 30 --yes
#   .\db-admin.ps1 vacuum
#   .\db-admin.ps1 integrity

param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Command,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Rest
)

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Join-Path $root '.venv\Scripts\python.exe'
$script = Join-Path $root 'db_admin.py'

if (-not (Test-Path $py)) {
    Write-Error "venv python not found at $py"
    exit 1
}
if (-not (Test-Path $script)) {
    Write-Error "db_admin.py not found at $script"
    exit 1
}

$argsList = @($Command)
if ($Rest) {
    $argsList += ($Rest | Where-Object { $_ -ne $null -and $_ -ne '' })
}

& $py $script @argsList
exit $LASTEXITCODE
