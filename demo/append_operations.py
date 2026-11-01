from pathlib import Path
import json,subprocess,shutil,re,wave,math
import imageio_ffmpeg
project=Path(__file__).resolve().parents[1];root=project.parent;build=root/'.build/video-v3';build.mkdir(parents=True,exist_ok=True);ff=imageio_ffmpeg.get_ffmpeg_exe()
original=build/'original-demo.mp4'
if not original.exists():
 probe=subprocess.run([ff,'-hide_banner','-i',str(project/'demo/演示视频.mp4')],capture_output=True,text=True,encoding='utf-8',errors='replace')
 m=re.search(r'Duration: (\d+):(\d+):([\d.]+)',probe.stderr)
 old_seconds=int(m[1])*3600+int(m[2])*60+float(m[3])
 if old_seconds>250:raise SystemExit('Input already has a tail or is too long; rebuild the 248-second base first')
 shutil.copyfile(project/'demo/演示视频.mp4',original)
with wave.open(str(build/'ops-narration.wav'),'rb') as w:spoken=w.getnframes()/w.getframerate()
seconds=max(40,math.ceil(spoken+1));assert seconds<=50
(build/'caption.txt').write_text('MySQL 运营指挥中心 · v3.0\n开发阶段实际界面快照；演练数据与实测服务分开标注',encoding='utf-8')
vf="scale=1280:720,pad=1280:912:0:96:color=0x102d52,drawtext=fontfile='C\\:/Windows/Fonts/msyh.ttc':textfile=caption.txt:fontcolor=white:fontsize=23:x=35:y=22:line_spacing=8"
tail=build/'ops-tail.mp4'
def run(args):
 r=subprocess.run([ff,'-hide_banner','-loglevel','error',*args],cwd=build,capture_output=True,text=True,encoding='utf-8',errors='replace')
 if r.returncode:raise RuntimeError(r.stderr)
 return r
run(['-y','-loop','1','-i',str(project/'docs/assets/运营大屏_1920x1080.jpg'),'-i',str(build/'ops-narration.wav'),'-vf',vf,'-af','apad','-t',str(seconds),'-r','25','-c:v','libx264','-preset','veryfast','-crf','22','-pix_fmt','yuv420p','-c:a','aac','-ar','22050','-ac','1',str(tail)])
(build/'video-concat.txt').write_text("file 'original-demo.mp4'\nfile 'ops-tail.mp4'\n",encoding='utf-8')
out=build/'demo-v3.mp4'
run(['-y','-f','concat','-safe','0','-i','video-concat.txt','-c','copy','-movflags','+faststart',str(out)])
r=subprocess.run([ff,'-hide_banner','-i',str(out)],capture_output=True,text=True,encoding='utf-8',errors='replace')
m=re.search(r'Duration: (\d+):(\d+):([\d.]+)',r.stderr);duration=int(m[1])*3600+int(m[2])*60+float(m[3]);assert duration<=300
decode=run(['-i',str(out),'-f','null','-']);assert not decode.stderr.strip(),decode.stderr
shutil.copyfile(out,project/'demo/演示视频.mp4')
validation=json.loads((project/'evidence/video_validation.json').read_text(encoding='utf-8'))
validation.update(duration_seconds=duration,under_5_minutes=True,scenes=23,full_decode_passed=True,bytes=out.stat().st_size,v3_addition='40-second narrated operations screenshot; actual UI still, not continuous live recording',media_probe=f'H264 1280x912 25fps / AAC mono / duration {duration:.2f}s')
(project/'evidence/video_validation.json').write_text(json.dumps(validation,ensure_ascii=False,indent=2),encoding='utf-8')
def stamp(s):
 ms=round(s*1000);return f'{ms//3600000:02d}:{ms//60000%60:02d}:{ms//1000%60:02d},{ms%1000:03d}'
p=project/'demo/演示字幕.srt';s=p.read_text(encoding='utf-8-sig');s+='\n23\n'+stamp(248)+' --> '+stamp(duration)+'\nMySQL运营指挥中心每5秒查询快照；运营、工单、设备、AI数字员工联动，管理接口与导出脱敏。界面为实际快照，网点设备为演练，本地服务健康为实测。\n';p.write_text(s,encoding='utf-8')
print(json.dumps({'seconds':duration,'bytes':out.stat().st_size,'full_decode':'pass','under_5_minutes':True},ensure_ascii=False))
