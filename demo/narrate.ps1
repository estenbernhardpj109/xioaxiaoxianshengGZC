Add-Type -AssemblyName System.Speech
$taskSynth=New-Object System.Speech.Synthesis.SpeechSynthesizer
$taskSynth.SelectVoice('Microsoft Huihui Desktop')
$taskSynth.Rate=2
$taskScenes=Get-Content -Raw -Encoding utf8 '.build/video/scenes.json' | ConvertFrom-Json
$taskIndex=0
foreach($taskScene in $taskScenes){
 $taskIndex++
 $taskWave=Join-Path (Get-Location) ('.build/video/{0:d2}.wav' -f $taskIndex)
 $taskSynth.SetOutputToWaveFile($taskWave)
 $taskSynth.Speak($taskScene.title+'。'+$taskScene.description)
 $taskSynth.SetOutputToNull()
}
$taskSynth.Dispose()
