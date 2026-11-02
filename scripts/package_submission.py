"""Create complete contest source archive, exclude runtime secrets, verify every member."""
from pathlib import Path
from zipfile import ZipFile,ZIP_DEFLATED
import hashlib
import json
import re

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT.parent/'交付成果'
OUT.mkdir(exist_ok=True)
files=[]
for p in sorted(ROOT.rglob('*')):
    if not p.is_file():continue
    rel=p.relative_to(ROOT)
    if any(x in ('.git','.runtime','.build','__pycache__','node_modules','.venv') or x.startswith('.chart-data-') for x in rel.parts):continue
    if p.name=='提交文件校验清单.json':continue
    if p.name.startswith('.env') and p.name!='.env.example':continue
    if p.suffix.lower() in ('.pyc','.pyo','.log','.pem','.key','.bak','.tmp'):continue
    if p.name in ('ops_manager_code.txt','.mysql_history'):raise RuntimeError('Private runtime file outside excluded directory')
    if not p.resolve().is_relative_to(ROOT.resolve()):raise RuntimeError('Source link points outside project')
    if re.search(rb'sk-[A-Za-z0-9_-]{20,}',p.read_bytes()):raise RuntimeError('Credential-like data found: '+str(rel))
    files.append(p)
manifest={'project':'知行银行智能体','version':'4.0.0','files':[{'path':p.relative_to(ROOT).as_posix(),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in files]}
mp=ROOT/'evidence/提交文件校验清单.json';mp.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8');files.append(mp)
target=OUT/'知行银行智能体_参赛提交包.zip'
with ZipFile(target,'w',ZIP_DEFLATED,compresslevel=9) as z:
    for p in files:z.write(p,'参赛作品/'+p.relative_to(ROOT).as_posix())
with ZipFile(target) as z:
    assert z.testzip() is None
    for rec in manifest['files']:assert hashlib.sha256(z.read('参赛作品/'+rec['path'])).hexdigest()==rec['sha256']
    assert not any('/.runtime/' in name or '/.git/' in name or name.endswith('/ops_manager_code.txt') for name in z.namelist())
(OUT/'SHA256.txt').write_text(hashlib.sha256(target.read_bytes()).hexdigest()+'  '+target.name+'\n',encoding='utf-8')
print(json.dumps({'file_count':len(files),'archive_bytes':target.stat().st_size,'member_hashes':'pass','archive_CRC':'pass','credential_scan':'pass'},ensure_ascii=False))
