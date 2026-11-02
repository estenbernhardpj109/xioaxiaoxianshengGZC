param([int]$Port=8011,[string]$Database='root',[string]$DbUser='root',[switch]$Simulation,[switch]$DeepSeek,[string]$Model='deepseek-flash')
$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonCommand=Get-Command python -ErrorAction SilentlyContinue
$pythonPath=if($pythonCommand){$pythonCommand.Source}else{Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'}
if(-not (Test-Path -LiteralPath $pythonPath)){throw '请先安装Python 3.10及以上，并将python加入PATH'}
$env:MYSQL_HOST='127.0.0.1'
$env:MYSQL_PORT='3306'
$env:MYSQL_DATABASE=$Database
$env:MYSQL_USER=$DbUser
$env:BANK_STORAGE='mysql'
$env:OPS_SIMULATOR=if($Simulation){'1'}else{'0'}
$dbSecret=Read-Host '输入本机MySQL密码（不会保存到文件）' -AsSecureString
$dbCredential=[System.Net.NetworkCredential]::new('', $dbSecret)
$env:MYSQL_PASSWORD=$dbCredential.Password
$setApiKey=$false
try {
    if($DeepSeek){
        $llmSecret=Read-Host '输入DeepSeek API Key（仅进入当前服务进程）' -AsSecureString
        $llmCredential=[System.Net.NetworkCredential]::new('', $llmSecret)
        $env:LLM_API_KEY=$llmCredential.Password
        $env:LLM_PROVIDER='deepseek'
        $env:LLM_MODEL=$Model
        $setApiKey=$true
    }
    & $pythonPath -c 'import mysql.connector, psutil'
    if($LASTEXITCODE -ne 0){throw '请先运行 python -m pip install -r requirements.txt'}
    Write-Host "注册与登录 http://127.0.0.1:$Port/login"
    Write-Host "银行工作台 http://127.0.0.1:$Port/app"
    Write-Host "数字人服务厅 http://127.0.0.1:$Port/assistant"
    Write-Host "运营指挥中心 http://127.0.0.1:$Port/ops"
    Write-Host '经理权限使用独立口令，启动后见 .runtime/ops_manager_code.txt'
    & $pythonPath backend/server.py --port $Port --storage mysql
} finally {
    Remove-Item Env:MYSQL_PASSWORD -ErrorAction SilentlyContinue
    if($setApiKey){Remove-Item Env:LLM_API_KEY -ErrorAction SilentlyContinue}
    Remove-Variable llmSecret,llmCredential,dbSecret,dbCredential -ErrorAction SilentlyContinue
}
