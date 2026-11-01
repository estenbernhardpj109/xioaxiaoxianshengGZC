param([int]$Port=8011,[string]$Database='root',[string]$DbUser='root',[switch]$Simulation)
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
try {
    & $pythonPath -c 'import mysql.connector, psutil'
    if($LASTEXITCODE -ne 0){throw '请先运行 python -m pip install -r requirements.txt'}
    Write-Host "银行工作台 http://127.0.0.1:$Port/"
    Write-Host "运营指挥中心 http://127.0.0.1:$Port/ops"
    Write-Host '经理权限使用独立口令，启动后见 .runtime/ops_manager_code.txt'
    & $pythonPath backend/server.py --port $Port --storage mysql
} finally {
    Remove-Item Env:MYSQL_PASSWORD -ErrorAction SilentlyContinue
    Remove-Variable dbSecret,dbCredential -ErrorAction SilentlyContinue
}
