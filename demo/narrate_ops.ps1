$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Speech
$projectTask=Split-Path $PSScriptRoot -Parent
$outputTask=Join-Path (Split-Path $projectTask -Parent) '.build/video-v3'
New-Item -ItemType Directory -Path $outputTask -Force | Out-Null
$voiceTask=New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $voiceTask.SelectVoice('Microsoft Huihui Desktop')
    $voiceTask.Rate=2
    $voiceTask.SetOutputToWaveFile((Join-Path $outputTask 'ops-narration.wav'))
    $voiceTask.Speak('新增运营指挥中心连接MySQL，每五秒读取数据库快照。领导查看运营、工单、设备和数字员工。经理按权限分派处理，工单与设备关联。管理接口和导出统一脱敏。网点设备为演练，本机服务健康为实测。')
} finally {$voiceTask.Dispose()}
