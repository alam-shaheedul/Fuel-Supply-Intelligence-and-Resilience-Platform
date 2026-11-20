param(
    [string]$BaseUrl = 'http://localhost:8000'
)

$ErrorActionPreference = 'Stop'
$BaseUrl = $BaseUrl.TrimEnd('/')

$health = Invoke-RestMethod "$BaseUrl/v1/health" -TimeoutSec 10
if ($health.status -ne 'ok' -or $health.database -ne 'ok') {
    throw 'The simulator or its database failed the health check.'
}
Write-Host 'PASS simulator and database health'

$instance = Invoke-RestMethod "$BaseUrl/v1/instance" -TimeoutSec 10
if ($instance.status -notin 'PAUSED', 'RUNNING' -or
    $null -eq $instance.tick -or $instance.tick_minutes -le 0) {
    throw 'Unexpected simulation instance response.'
}
Write-Host "PASS simulation instance ($($instance.status), tick $($instance.tick))"

$baselineCounts = [ordered]@{ regions = 2; depots = 2; stations = 4; routes = 6 }
foreach ($resource in $baselineCounts.Keys) {
    $items = Invoke-RestMethod "$BaseUrl/v1/$resource" -TimeoutSec 10
    if ($items.Count -ne $baselineCounts[$resource]) {
        throw "Unexpected baseline count for $resource. Expected $($baselineCounts[$resource]), received $($items.Count)."
    }
    Write-Host "PASS $resource ($($items.Count))"
}

foreach ($path in '/docs', '/admin') {
    $page = Invoke-WebRequest "$BaseUrl$path" -UseBasicParsing -TimeoutSec 10
    if ($page.StatusCode -ne 200 -or $page.Headers['Content-Type'] -notlike 'text/html*') {
        throw "Expected an HTML page at $path."
    }
    Write-Host "PASS $path"
}
