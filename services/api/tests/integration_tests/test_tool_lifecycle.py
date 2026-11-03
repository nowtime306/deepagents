"""Native container lifecycle regression for BUG-EXP004; never signals host processes."""

import json
import os
import subprocess

import pytest

from app.tool_transports import BRIDGE


@pytest.mark.skipif(os.getenv("RUN_LOCAL_SANDBOX") != "1", reason="Requires trusted local coding image")
@pytest.mark.parametrize("transport", ["stdio", "acp"])
@pytest.mark.parametrize("outcome", ["success", "failure", "timeout", "cancel"])
def test_protocol_descendants_are_reaped(transport, outcome):
    agent = r'''
import json,os,subprocess,sys,time
descendant=("import subprocess,sys,time;subprocess.Popen([sys.executable,'-c',"
 "'import time;time.sleep(120)','lifecycle-leaf'],start_new_session=True);time.sleep(120)")
subprocess.Popen([sys.executable,'-c',descendant,'lifecycle-child'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
time.sleep(.15)
open('/workspace/ready','w').close()
for line in sys.stdin:
 m=json.loads(line)
 if 'id' not in m: continue
 if MODE in ('timeout','cancel'): time.sleep(120)
 if MODE=='failure': print('malformed JSON',flush=True);continue
 if m['method']=='initialize': result={'protocolVersion':m['params']['protocolVersion']}
 elif m['method']=='session/new': result={'sessionId':'local'}
 elif m['method']=='session/prompt': result={'stopReason':'end_turn'}
 else: result={'tools':[]}
 print(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':result}),flush=True)
'''.replace("MODE", repr(outcome))
    supervisor = r'''
import json,os,signal,subprocess,sys,time
body=json.loads(sys.stdin.read());os.makedirs('/workspace',exist_ok=True)
p=subprocess.Popen(['python','-c',body['bridge']],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
p.stdin.write(json.dumps(body['request']));p.stdin.close();p.stdin=None
if body['outcome']=='cancel':
 deadline=time.monotonic()+10
 while not os.path.exists('/workspace/ready'):
  if time.monotonic()>deadline: raise RuntimeError('agent not ready')
  time.sleep(.02)
 os.kill(p.pid,signal.SIGTERM)
out,err=p.communicate(timeout=55)
remaining=[]
for pid in os.listdir('/proc'):
 if not pid.isdigit(): continue
 try:
  with open('/proc/'+pid+'/cmdline','rb') as f: args=f.read().split(b'\0')
  if b'lifecycle-child' in args or b'lifecycle-leaf' in args: remaining.append(pid)
 except FileNotFoundError: pass
print(json.dumps({'exit':p.returncode,'remaining':remaining,'error':err[-200:]}))
'''
    request = {"transport": transport, "operation": "discover", "text": "local",
               "command": ["/usr/local/bin/python", "-u", "-c", agent]}
    result = subprocess.run(
        ["docker", "run", "--rm", "--label", "owner-agent.test=tool-lifecycle", "--network", "none",
         "--memory", "128m", "--pids-limit", "64",
         "--cpus", "0.25", "-i", "--entrypoint", "python", "owner-agent/coding:dev", "-c", supervisor],
        input=json.dumps({"bridge": BRIDGE, "request": request, "outcome": outcome}),
        capture_output=True, text=True, timeout=65,
    )
    assert result.returncode == 0, result.stderr
    actual = json.loads(result.stdout)
    assert actual["remaining"] == [], actual
    assert (actual["exit"] == 0) == (outcome == "success"), actual
