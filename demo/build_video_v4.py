"""Assemble captured v4 UI stills with Chinese narration and burned-in descriptions.

Run narrate_v4.ps1 first on Windows. Optional authoring dependency: imageio-ffmpeg.
This never drives a browser, executes a bank transaction or edits a live database.
"""
from pathlib import Path
import hashlib
import json
import math
import os
import shutil
import subprocess
import wave

PROJECT=Path(__file__).resolve().parents[1]
WORK=PROJECT.parent/'.build/video-v4'
WORK.mkdir(parents=True,exist_ok=True)
scenes=json.loads((PROJECT/'demo/v4_scenes.json').read_text(encoding='utf-8'))
voices=[]
for i,scene in enumerate(scenes,1):
    with wave.open(str(WORK/f'{i:02d}.wav'),'rb') as audio:
        rate,channels,width=audio.getframerate(),audio.getnchannels(),audio.getsampwidth()
        pcm=audio.readframes(audio.getnframes())
    duration=len(pcm)/(rate*channels*width)
    scene['seconds']=max(scene['seconds'],math.ceil(duration+.4))
    voices.append(pcm+bytes(int(scene['seconds']*rate*channels*width)-len(pcm)))
total=sum(s['seconds'] for s in scenes)
if total>300:raise RuntimeError('Narration exceeds five minutes; revise text or speech rate, never truncate silently')
with wave.open(str(WORK/'narration.wav'),'wb') as audio:
    audio.setnchannels(channels);audio.setsampwidth(width);audio.setframerate(rate);audio.writeframes(b''.join(voices))
font=Path('C:/Windows/Fonts/msyh.ttc')
if not font.exists():raise RuntimeError('Install/choose a licensed Chinese font for video captions')
(WORK/'fonts').mkdir(exist_ok=True)
shutil.copy2(font,WORK/'fonts/msyh.ttc')  # Private render cache, never included in source archive.
ass=['[Script Info]','ScriptType: v4.00+','PlayResX: 1920','PlayResY: 1200','WrapStyle: 0','',
     '[V4+ Styles]','Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding',
     'Style: Title,Microsoft YaHei,36,&H00FFFFFF,&H00FFFFFF,&H00102D50,&H00102D50,-1,0,0,0,100,100,0,0,1,0,0,7,50,50,18,1',
     'Style: Description,Microsoft YaHei,25,&H00D9E8F8,&H00FFFFFF,&H00102D50,&H00102D50,0,0,0,0,100,100,0,0,1,0,0,7,50,50,70,1',
     '', '[Events]','Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text']
def ass_time(n):return f'{int(n)//3600}:{int(n)//60%60:02d}:{int(n)%60:02d}.00'
def srt_time(n):return f'{int(n)//3600:02d}:{int(n)//60%60:02d}:{int(n)%60:02d},000'
def safe(text):return text.replace('\\','／').replace('{','（').replace('}','）').replace('\n',' ')
start=0;concat=[];srt=[]
for i,scene in enumerate(scenes,1):
    frame=PROJECT/'demo/frames_v4'/scene['frame']
    if not frame.exists():raise FileNotFoundError(frame)
    local=f'frame-{i:02d}.png';shutil.copy2(frame,WORK/local)
    concat.extend([f"file '{local}'",f"duration {scene['seconds']}"])
    end=start+scene['seconds']
    ass.extend([f'Dialogue: 0,{ass_time(start)},{ass_time(end)},Title,,0,0,0,,{i:02d}  {safe(scene["title"])}',
                f'Dialogue: 0,{ass_time(start)},{ass_time(end)},Description,,0,0,0,,{safe(scene["description"])}'])
    srt.extend([str(i),f'{srt_time(start)} --> {srt_time(end)}',scene['narration'],''])
    start=end
concat.append(f"file 'frame-{len(scenes):02d}.png'")
(WORK/'concat.txt').write_text('\n'.join(concat),encoding='utf-8')
(WORK/'captions.ass').write_text('\n'.join(ass),encoding='utf-8-sig')
(PROJECT/'demo/演示字幕.srt').write_text('\n'.join(srt),encoding='utf-8')
ff=os.environ.get('FFMPEG') or shutil.which('ffmpeg')
if not ff:
    import imageio_ffmpeg
    ff=imageio_ffmpeg.get_ffmpeg_exe()
out=PROJECT/'demo/演示视频.mp4'
command=[ff,'-y','-hide_banner','-loglevel','error','-f','concat','-safe','0','-i','concat.txt','-i','narration.wav',
    '-vf',"scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=white,pad=1920:1200:0:120:color=0x102D50,subtitles=captions.ass:fontsdir=fonts,fps=25,format=yuv420p",
    '-c:v','libx264','-preset','fast','-crf','21','-c:a','aac','-b:a','128k','-t',str(total),'-movflags','+faststart',str(out)]
result=subprocess.run(command,cwd=WORK,capture_output=True,text=True,encoding='utf-8',errors='replace')
if result.returncode:raise RuntimeError(result.stderr)
decoded=subprocess.run([ff,'-v','error','-i',str(out),'-f','null','-'],capture_output=True)
if decoded.returncode:raise RuntimeError('Final MP4 full-decode validation failed')
evidence={'version':'4.0.0','date':'2026-11-02','file':'demo/演示视频.mp4','duration_seconds':total,
    'width':1920,'height':1200,'page_canvas_width':1920,'page_canvas_height':1080,'fps':25,'codec':'h264','audio_codec':'aac',
    'scenes':len(scenes),'kind':'actual_v4_UI_action_stills_with_narration_and_burned_captions','continuous_screen_recording':False,
    'source':'isolated synthetic MySQL UI validation database','audio':'local synthetic Chinese narration',
    'transactions_in_recording':'proposed transfers and birthday reservations were cancelled, no balance debits',
    'under_5_minutes':total<=300,'full_decode':'pass','bytes':out.stat().st_size,'sha256':hashlib.sha256(out.read_bytes()).hexdigest()}
(PROJECT/'evidence/video_validation.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'duration_seconds':total,'bytes':out.stat().st_size,'scenes':len(scenes),'full_decode':'pass'}))
