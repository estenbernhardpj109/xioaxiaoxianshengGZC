"""Read a key from stdin without persisting it, then probe read-only Tool Calling."""
import os, sys, json, time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
key=input().strip()
os.environ['LLM_API_KEY']=key
from backend.agent import llm
from backend.agent.tools import tool_schemas
started=time.perf_counter()
result={'provider':'deepseek','model':llm._config()[1], 'kind':'read_only_tool_call', 'key_stored':False}
try:
    msg=llm.chat_with_tools([{'role':'system','content':'Only choose the query_balance tool. Do not write account information.'},{'role':'user','content':'查询我的余额'}], [s for s in tool_schemas() if s['function']['name']=='query_balance'])
    calls=msg.get('tool_calls',[])
    result.update(success=any(c.get('function',{}).get('name')=='query_balance' for c in calls),operations=[c['function']['name'] for c in calls])
except Exception as e:
    result.update(success=False,error_type=type(e).__name__,http_status=getattr(e,'code',None))
finally:
    os.environ.pop('LLM_API_KEY',None)
result['elapsed_ms']=round((time.perf_counter()-started)*1000)
p=Path(__file__).resolve().parents[1]/'evidence'/'deepseek_probe.json';p.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False))
