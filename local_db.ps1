param(
    [ValidateSet('up', 'status', 'stop', 'sql')]
    [string]$Action = 'up'
)
$ErrorActionPreference = 'Stop'
$dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
if ($dockerCommand) {
    $dockerPath = $dockerCommand.Source
} else {
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs/DockerDesktop/resources/bin/docker.exe'),
        (Join-Path $env:ProgramFiles 'Docker/Docker/resources/bin/docker.exe')
    )
    $dockerPath = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if (-not $dockerPath) {
    throw 'Docker Desktop was not found. Install it and start it before running this script.'
}
$composePath = Join-Path $PSScriptRoot 'compose.yaml'
switch ($Action) {
    'up' { & $dockerPath compose --project-directory $PSScriptRoot -f $composePath up -d --wait }
    'status' { & $dockerPath compose --project-directory $PSScriptRoot -f $composePath ps }
    'stop' { & $dockerPath compose --project-directory $PSScriptRoot -f $composePath stop }
    'sql' { & $dockerPath compose --project-directory $PSScriptRoot -f $composePath exec mysql mysql --user=jonhbrae --password ex2_db }
}
if ($LASTEXITCODE -ne 0) {
    throw "Docker command failed with exit code $LASTEXITCODE. Check that Docker Desktop is running."
}
