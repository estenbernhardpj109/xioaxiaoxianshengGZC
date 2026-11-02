param([string]$OutputDirectory='')
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Speech
$projectTask=Split-Path $PSScriptRoot -Parent
if(-not $OutputDirectory){$OutputDirectory=Join-Path (Split-Path $projectTask -Parent) '.build/video-v4'}
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$scenesTask=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'v4_scenes.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$voiceTask=New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $voiceTask.SelectVoice('Microsoft Huihui Desktop')
    $voiceTask.Rate=2
    for($sceneTask=0;$sceneTask -lt $scenesTask.Count;$sceneTask++){
        $audioTask=Join-Path $OutputDirectory ('{0:D2}.wav' -f ($sceneTask+1))
        $voiceTask.SetOutputToWaveFile($audioTask)
        $voiceTask.Speak($scenesTask[$sceneTask].narration)
        $voiceTask.SetOutputToNull()
    }
} finally {$voiceTask.Dispose()}
