"""R-TOOL-006: protocol pipe pressure cannot prevent subtree cleanup."""

import json
import os
import subprocess

import pytest

from app.tool_transports import BRIDGE


@pytest.mark.skipif(os.getenv("RUN_LOCAL_SANDBOX") != "1", reason="Requires trusted local coding image")
@pytest.mark.parametrize("mode", ["large_cancel", "prompt_write_timeout", "reply_write_timeout", "frame_limit"])
def test_protocol_writes_are_bounded_and_preserve_unrelated_process(mode):
    agent = r'''
import json,subprocess,sys,time
subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)','backpressure-descendant'],start_new_session=True,
 stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
for line in sys.stdin:
 m=json.loads(line)
 if MODE in ('reply_write_timeout','frame_limit'):
  size=120000 if MODE=='reply_write_timeout' else 130990
  print(json.dumps({'jsonrpc':'2.0','id':'x'*size,'method':'client/unsupported','params':{}}),flush=True)
  time.sleep(120)
 if m['method']=='initialize': result={'protocolVersion':1}
 elif m['method']=='session/new': result={'sessionId':'x'*120000}
 elif m['method']=='session/prompt': result={'stopReason':'end_turn'}
 else: continue
 print(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':result}),flush=True)
 if (m['method']=='session/new' and MODE=='prompt_write_timeout') or m['method']=='session/prompt':time.sleep(120)
'''.replace("MODE", repr(mode))
    supervisor = r'''
import json,os,subprocess,sys,time
body=json.loads(sys.stdin.read());os.makedirs('/workspace',exist_ok=True)
unrelated=subprocess.Popen(['python','-c','import time;time.sleep(120)','unrelated-sibling'])
try:
 start=time.monotonic()
 p=subprocess.Popen(['python','-c',body['bridge']],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
 out,err=p.communicate(json.dumps(body['request']),timeout=50)
 elapsed=time.monotonic()-start;remaining=[]
 for pid in os.listdir('/proc'):
  if not pid.isdigit():continue
  try:
   with open('/proc/'+pid+'/cmdline','rb') as f: args=f.read().split(b'\0')
   if b'backpressure-descendant' in args:remaining.append(pid)
  except FileNotFoundError:pass
 print(json.dumps({'exit':p.returncode,'elapsed':elapsed,'remaining':remaining,
   'unrelated_alive':unrelated.poll() is None,'error':err[-400:]}))
finally:
 unrelated.kill();unrelated.wait()
'''
    transport = "stdio" if mode in {"reply_write_timeout", "frame_limit"} else "acp"
    request = {"transport": transport, "operation": "discover", "text": "local",
               "command": ["/usr/local/bin/python", "-u", "-c", agent]}
    result = subprocess.run(
        ["docker", "run", "--rm", "--pull=never", "--label", "owner-agent.test=tool-backpressure",
         "--network", "none", "--memory", "128m", "--pids-limit", "64", "--cpus", ".25", "-i",
         "--entrypoint", "python", "owner-agent/coding:dev", "-c", supervisor],
        input=json.dumps({"bridge": BRIDGE, "request": request}), capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    actual = json.loads(result.stdout)
    assert actual["remaining"] == [] and actual["unrelated_alive"], actual
    if mode == "large_cancel":
        assert actual["exit"] == 0 and actual["elapsed"] < 5, actual
    elif mode == "frame_limit":
        assert actual["exit"] != 0 and actual["elapsed"] < 5, actual
        assert "PROTOCOL_INPUT_LIMIT" in actual["error"], actual
    else:
        assert actual["exit"] != 0 and 44 <= actual["elapsed"] < 49, actual
        assert "PROTOCOL_WRITE_TIMEOUT" in actual["error"], actual
