param(
    [ValidateSet('Setup','Start','Stop','Restart','Status')][string]$Action='Status',
    [string]$ImportEnv,
    [string]$ImportKnowledgeEnv,
    [ValidateSet('All','Backend','Frontend','Knowledge')][string]$Target='All'
)
$ErrorActionPreference='Stop'
$repoRoot=Split-Path -Parent $PSScriptRoot
$python=Join-Path $repoRoot '.venv/Scripts/python.exe'
$privateRoot=Join-Path $repoRoot '.local'
$previousUtf8=$env:PYTHONUTF8
$previousConfig=$env:RC_CONFIG_FILE
$env:PYTHONUTF8='1'
$env:RC_CONFIG_FILE=Join-Path $privateRoot 'dev.env'
New-Item -ItemType Directory -Path $privateRoot -Force | Out-Null
$rootHash=[Convert]::ToHexString([Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes($repoRoot))).Substring(0,20)
$mutex=[Threading.Mutex]::new($false,"Local\ResearchCopilot-$rootHash")
$locked=$false

function Get-Listener([int]$Port) {
    $connections=@(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    $owners=@($connections | Select-Object -ExpandProperty OwningProcess -Unique)
    if($owners.Count -gt 1){throw "Port $Port has multiple process owners; refusing to operate"}
    if($owners.Count -eq 1){return [int]$owners[0]}
    return $null
}

function Get-State($Service) {
    $ownerId=Get-Listener $Service.Port
    $owned=$false; $ready=$false
    if($ownerId){
        $info=Get-CimInstance Win32_Process -Filter "ProcessId=$ownerId"
        $owned=([string]$info.CommandLine).IndexOf($Service.Marker,[StringComparison]::OrdinalIgnoreCase) -ge 0
        if($owned){
            try {
                $response=Invoke-RestMethod $Service.Probe -TimeoutSec 2
                $ready=$response.product -eq 'research-copilot' -and $response.instance_id -eq $config.instance_id
                if($Service.Name -eq 'knowledge'){$ready=$ready -and $response.service -eq 'knowledge'}
            } catch { $ready=$false }
        }
    }
    return [ordered]@{name=$Service.Name;port=$Service.Port;pid=$ownerId;owned=$owned;ready=$ready}
}

function Stop-Owned($Service) {
    $state=Get-State $Service
    if(-not $state.pid){return}
    if(-not $state.owned){throw "Port $($Service.Port) is not this repository service; refusing to stop"}
    # Re-read ownership immediately before mutation; persisted PID lists are not authority.
    $current=Get-CimInstance Win32_Process -Filter "ProcessId=$($state.pid)"
    if(([string]$current.CommandLine).IndexOf($Service.Marker,[StringComparison]::OrdinalIgnoreCase) -lt 0){throw 'Process identity changed'}
    Stop-Process -Id $state.pid -ErrorAction Stop
    $deadline=(Get-Date).AddSeconds(10)
    while((Get-Date) -lt $deadline -and (Get-Listener $Service.Port)){Start-Sleep -Milliseconds 150}
    if(Get-Listener $Service.Port){throw "Port $($Service.Port) did not stop"}
}

try {
    try {$locked=$mutex.WaitOne(10000)} catch [Threading.AbandonedMutexException] {$locked=$true}
    if(-not $locked){throw 'Another service operation is in progress'}
    if($Action -eq 'Setup') {
        if($Target -in @('All','Backend')){
            & uv sync --frozen --project $repoRoot
            if($LASTEXITCODE -ne 0){throw 'Frozen backend installation failed'}
        }
        if($Target -in @('All','Frontend')){
            Push-Location -LiteralPath (Join-Path $repoRoot 'frontend')
            try {& pnpm install --frozen-lockfile --ignore-scripts; if($LASTEXITCODE -ne 0){throw 'Frozen frontend installation failed'}}
            finally {Pop-Location}
        }
        if($Target -in @('All','Knowledge')){
            & uv sync --frozen --project (Join-Path $repoRoot 'services/knowledge')
            if($LASTEXITCODE -ne 0){throw 'Frozen knowledge installation failed'}
        }
        $initArgs=@((Join-Path $PSScriptRoot 'local_init.py'))
        if($ImportEnv){$initArgs+=@('--import-env',$ImportEnv)}
        & $python @initArgs
        if($LASTEXITCODE -ne 0){throw 'Local configuration initialization failed'}
        if($ImportKnowledgeEnv){
            & $python (Join-Path $PSScriptRoot 'knowledge_init.py') --import-env $ImportKnowledgeEnv
            if($LASTEXITCODE -ne 0){throw 'Knowledge configuration initialization failed'}
        }
    }
    if(-not (Test-Path -LiteralPath $python)){throw 'Run services.ps1 Setup first'}
    $configText=& $python (Join-Path $PSScriptRoot 'local_init.py') --public
    if($LASTEXITCODE -ne 0){throw 'Local configuration is invalid'}
    $config=$configText | ConvertFrom-Json
    $services=@(
        @{Name='backend';Python=$python;Port=[int]$config.api_port;Entry=(Join-Path $PSScriptRoot 'run_backend.py');Marker=(Join-Path $PSScriptRoot 'run_backend.py');Probe=($config.api_url+'/health')},
        @{Name='frontend';Python=$python;Port=[int]$config.frontend_port;Entry=(Join-Path $PSScriptRoot 'run_frontend.py');Marker=(Join-Path $repoRoot 'frontend/node_modules/vite/bin/vite.js');Probe="http://127.0.0.1:$($config.frontend_port)/__rc_runtime"}
    )
    if($config.knowledge_enabled){
        $services=@(@{Name='knowledge';Python=(Join-Path $repoRoot 'services/knowledge/.venv/Scripts/python.exe');Port=[int]$config.knowledge_port;Entry=(Join-Path $PSScriptRoot 'run_knowledge.py');Marker=(Join-Path $PSScriptRoot 'run_knowledge.py');Probe="http://127.0.0.1:$($config.knowledge_port)/health"})+$services
    }
    $allServices=$services
    if($Target -ne 'All'){$services=@($services | Where-Object {$_.Name -eq $Target})}
    if($Target -eq 'Knowledge' -and -not $config.knowledge_enabled -and $Action -ne 'Setup') {throw 'Knowledge is not configured; run Setup -ImportKnowledgeEnv first'}
    if($Action -in @('Stop','Restart')) {
        # Check both before stopping either, so a foreign owner cannot cause a partial stop.
        foreach($service in $services){$state=Get-State $service;if($state.pid -and -not $state.owned){throw "Port $($service.Port) belongs to another process"}}
        foreach($service in $services){Stop-Owned $service}
    }
    if($Action -in @('Start','Restart')) {
        if(($services.Name -contains 'frontend') -and -not (Test-Path -LiteralPath (Join-Path $repoRoot 'frontend/node_modules/vite/bin/vite.js'))){throw 'Run Setup to install frontend dependencies'}
        foreach($service in $services){if(-not (Test-Path -LiteralPath $service.Python)){throw "Run Setup to install $($service.Name) dependencies"}}
        foreach($service in $services){$state=Get-State $service;if($state.pid -and -not $state.owned){throw "Port $($service.Port) belongs to another process; refusing to replace it"}}
        $started=@()
        try {
            New-Item -ItemType Directory -Path $config.log_dir -Force | Out-Null
            foreach($service in $services){
                if(-not (Get-Listener $service.Port)){
                    Start-Process -FilePath $service.Python -ArgumentList @('-u',('"{0}"' -f $service.Entry)) -WorkingDirectory $repoRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $config.log_dir "$($service.Name).stdout.log") -RedirectStandardError (Join-Path $config.log_dir "$($service.Name).stderr.log") | Out-Null
                    $started+=$service
                }
            }
            $deadline=(Get-Date).AddSeconds(45)
            do {
                $states=@($services | ForEach-Object {Get-State $_})
                if(@($states | Where-Object {-not $_.ready}).Count -eq 0){break}
                Start-Sleep -Milliseconds 250
            } while((Get-Date) -lt $deadline)
            if(@($states | Where-Object {-not $_.ready}).Count){throw 'Services did not become ready; see repository logs'}
        } catch {
            foreach($service in $started){try {Stop-Owned $service} catch {}}
            throw
        }
    }
    $knowledgeStatus='not_configured'
    if($config.knowledge_enabled){$knowledgeStatus='unavailable';if((Get-State $allServices[0]).ready){$knowledgeStatus='ready'}}
    $result=[ordered]@{repository=$repoRoot;instance_id=$config.instance_id;target=$Target;services=@($allServices | ForEach-Object {Get-State $_});knowledge=@{port=[int]$config.knowledge_port;status=$knowledgeStatus};checked_at=(Get-Date).ToString('o')}
    $result | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $privateRoot 'services.json') -Encoding utf8
    $result | ConvertTo-Json -Depth 5
} finally {
    if($locked){$mutex.ReleaseMutex()}
    $mutex.Dispose()
    $env:PYTHONUTF8=$previousUtf8
    $env:RC_CONFIG_FILE=$previousConfig
}
