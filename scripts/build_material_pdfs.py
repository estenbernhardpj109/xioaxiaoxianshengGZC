"""Optional material builder. Install reportlab, Pillow and pypdf separately.

Application runtime dependencies are listed in requirements.txt; this is an authoring tool.
"""
from pathlib import Path
import html
import json
import re
from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle,Image,PageBreak,Flowable,CondPageBreak
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.pagesizes import A4
from PIL import Image as PILImage

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs'
font=Path('C:/Windows/Fonts/simsun.ttc')
if not font.exists():raise SystemExit('Set a locally available Chinese TTF font in this material builder')
pdfmetrics.registerFont(TTFont('CN',str(font),subfontIndex=0))
blue=colors.HexColor('#174f8f');muted=colors.HexColor('#657b93')
styles={
 'body':ParagraphStyle('body',fontName='CN',fontSize=10,leading=16,spaceAfter=7,wordWrap='CJK',textColor=colors.HexColor('#233a52')),
 'h1':ParagraphStyle('h1',fontName='CN',fontSize=22,leading=29,spaceAfter=16,textColor=blue),
 'h2':ParagraphStyle('h2',fontName='CN',fontSize=14.5,leading=22,spaceBefore=13,spaceAfter=9,keepWithNext=False,textColor=blue),
 'h3':ParagraphStyle('h3',fontName='CN',fontSize=12,leading=19,spaceBefore=10,spaceAfter=7,keepWithNext=False,textColor=blue),
 'small':ParagraphStyle('small',fontName='CN',fontSize=8,leading=12,wordWrap='CJK',textColor=muted),
 'cell':ParagraphStyle('cell',fontName='CN',fontSize=8.2,leading=12.5,wordWrap='CJK'),
 'code':ParagraphStyle('code',fontName='CN',fontSize=8.2,leading=12.8,spaceAfter=6,backColor=colors.HexColor('#f1f5fb'),wordWrap='CJK'),
}


def clean(s):
    s=re.sub(r'\[([^\]]+)\]\(([^)]+)\)',r'\1（\2）',s)
    s=html.escape(s)
    s=re.sub(r'`([^`]+)`',r'<font color="#246eb5">\1</font>',s)
    return re.sub(r'\*\*(.*?)\*\*',r'<b>\1</b>',s)


def picture(name,width=475):
    p=OUT/'assets'/name
    if not p.is_file():return []
    with PILImage.open(p) as im:w,h=im.size
    return [Spacer(1,8),Image(str(p),width=width,height=width*h/w),Spacer(1,7)]


class Architecture(Flowable):
    def __init__(self):super().__init__();self.width=475;self.height=215
    def draw(self):
        c=self.canv
        def box(x,y,w,h,lines,fill='#edf4fd'):
            c.setFillColor(colors.HexColor(fill));c.setStrokeColor(colors.HexColor('#c8daee'));c.roundRect(x,y,w,h,5,fill=1,stroke=1)
            c.setFillColor(blue);c.setFont('CN',9)
            for i,t in enumerate(lines):c.drawCentredString(x+w/2,y+h/2+4-(i*13),t)
        def arrow(x,y,x2,y2):
            c.setStrokeColor(colors.HexColor('#7799bc'));c.setLineWidth(.8);c.line(x,y,x2,y2)
            from math import hypot
            length=hypot(x2-x,y2-y) or 1;dx=(x2-x)/length;dy=(y2-y)/length
            for sign in (-1,1):c.line(x2,y2,x2-4*dx+sign*2*dy,y2-4*dy-sign*2*dx)
        for x,lines in [(0,['注册与登录','独立模拟账户']),(162,['工作台与数字人','咨询 / 引导 / 办理']),(324,['运营指挥中心','领导只读 / 经理处置'])]:box(x,173,150,40,lines)
        for x,lines in [(0,['身份与请求网关','scrypt / 会话 / CSRF']),(162,['知识问答与金融DAG','固定工具 / 独立授权']),(324,['运营与关联工单','白名单DTO / 版本检查'])]:box(x,116,150,40,lines)
        for x in (75,237,399):arrow(x,173,x,156);arrow(x,116,x,94)
        box(0,55,475,39,['MySQL InnoDB：15张项目表','身份与账户 / 事务状态 / 精确审计 / 运营事件'],'#dfedfc')
        box(0,0,145,37,['设备与服务采集','5秒心跳 / 预警工单'])
        box(164,0,145,37,['工单状态机','分派 / 处理 / 解决'])
        box(328,0,147,37,['脱敏图表与导出','12类客户图 / 6类运营图'])
        arrow(74,37,74,55);arrow(237,55,237,37);arrow(400,55,400,37)


def footer(c,d):
    c.saveState();c.setFont('CN',8);c.setFillColor(muted)
    c.drawString(44,25,'知行银行智能体  v4.0  独立身份与数字人金融沙箱')
    c.drawRightString(A4[0]-44,25,str(d.page));c.restoreState()


def table(rows):
    n=max(map(len,rows));widths=([90,238,147] if n==3 else [72,151,160,92] if n==4 else [475/n]*n)
    data=[[Paragraph(clean(v),styles['cell']) for v in row] for row in rows]
    t=Table(data,colWidths=widths,repeatRows=1,hAlign='LEFT')
    t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#e8f0fa')),('VALIGN',(0,0),(-1,-1),'TOP'),('GRID',(0,0),(-1,-1),.35,colors.HexColor('#d9e2ee')),('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),7),('LEFTPADDING',(0,0),(-1,-1),7),('RIGHTPADDING',(0,0),(-1,-1),7),('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#f8fafd')])]))
    return t


def build(src,target,title):
    lines=(OUT/src).read_text(encoding='utf-8-sig').splitlines();story=[];i=0
    while i<len(lines):
        line=lines[i].strip()
        if not line:i+=1;continue
        if line.startswith('```'):
            lang=line[3:];block=[];i+=1
            while i<len(lines) and not lines[i].startswith('```'):block.append(lines[i]);i+=1
            if lang=='mermaid':
                if '鲜花' in '\n'.join(block):story+=picture('任务DAG.png')
                else:story+=[Spacer(1,8),Architecture(),Spacer(1,12)]
            else:
                for text in block:story.append(Paragraph(clean(text) or ' ',styles['code']))
            i+=1;continue
        if line.startswith('|'):
            rows=[]
            while i<len(lines) and lines[i].strip().startswith('|'):
                parts=[x.strip() for x in lines[i].strip().strip('|').split('|')]
                if not all(re.fullmatch(r'[-: ]+',x) for x in parts):rows.append(parts)
                i+=1
            story.extend([table(rows),Spacer(1,9)]);continue
        if line.startswith('# '):story.append(Paragraph(clean(line[2:]),styles['h1']))
        elif line.startswith('### '):story.extend([CondPageBreak(70),Paragraph(clean(line[4:]),styles['h3'])])
        elif line.startswith('## '):story.extend([CondPageBreak(78),Paragraph(clean(line[3:]),styles['h2'])])
        else:story.append(Paragraph(clean(line),styles['body']))
        i+=1
    if src=='作品报告.md':
        story.extend([PageBreak(),Paragraph('界面与可视化记录',styles['h1']),Paragraph('下图为开发阶段实际界面快照；运行数据持续变化，以页面时间和MySQL快照为准。全部金融与网点终端均为模拟。',styles['body'])])
        story+=picture('运营大屏_v4.png')
        sample=json.loads((ROOT/'evidence/operations_snapshot_example.json').read_text(encoding='utf-8'))
        from datetime import datetime,timezone,timedelta
        stamp=datetime.fromtimestamp(sample['meta']['generated_at'],timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
        story.append(Paragraph('独立运营快照示例：'+stamp+'，全部网点，近7日。图像与表格为分别采集，不要求动态数值相同。',styles['small']))
        k=sample['kpis'];story.append(table([['指标','快照值','口径'],['流水笔数',str(k['transactions']),'入账与出账记录'],['流水金额',f"{k['turnover']:,.2f}元",'绝对流水合计，非利润'],['待办工单',str(k['backlog']),'期间创建且未解决'],['AI请求',str(k['ai_requests']),'演练与实际应用分别计数'],['设备正常',f"{k['devices_online']}/{k['devices_total']}",'当前状态与心跳']]))
        story.extend([PageBreak(),Paragraph('注册与数字人服务厅',styles['h1']),Paragraph('统一蓝白风格、规范路由与身份标识；数字人解答附依据，业务授权沿用既有策略。截图为独立模拟库的开发阶段实际界面。',styles['body'])])
        story+=picture('登录界面_v4.png',475);story+=picture('数字人服务厅_v4.png',475)
        story.extend([PageBreak(),Paragraph('独立注册与多源数据展示',styles['h1']),Paragraph('注册页最小化采集信息；客户数据全景只查询当前账户。演示样本与注册账户隔离，空账户不伪造历史流水。',styles['body'])])
        story+=picture('注册界面_v4.png',475);story+=picture('数据全景_v4.png',475)
        story.extend([PageBreak(),Paragraph('客户工作台与行动依据',styles['h1'])]);story+=picture('工作台_v4.png',475)
    if src=='安全自评报告.md':
        story.extend([CondPageBreak(410),Paragraph('强验证交互证据',styles['h1']),Paragraph('确认窗明确风险、参数与模拟身份因子，验证码不是独立真实短信通道。新版登录认证不会替代交易授权。',styles['body'])]);story+=picture('强验证_v4.png',475)
    doc=SimpleDocTemplate(str(OUT/target),pagesize=A4,leftMargin=58,rightMargin=58,topMargin=44,bottomMargin=45,title=title,author='知行银行智能体')
    doc.build(story,onFirstPage=footer,onLaterPages=footer)
    print(target)


if __name__=='__main__':
    for src,target,title in [('作品报告.md','技术文档.pdf','知行银行智能体技术文档 v4.0'),('安全自评报告.md','安全自评报告.pdf','知行银行智能体安全自评 v4.0'),('部署文档.md','部署说明.pdf','知行银行智能体部署说明 v4.0'),('规则核对与提交清单.md','规则与提交清单.pdf','规则与提交清单 v4.0')]:
        build(src,target,title)
