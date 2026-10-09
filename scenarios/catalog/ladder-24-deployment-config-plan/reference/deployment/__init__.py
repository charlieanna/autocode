import heapq
import json
import os
import re
import tempfile
from pathlib import Path

_PATTERN=re.compile(r'\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^{}]*))?\}')

def _resolve(value,environment):
    if not isinstance(value,str): raise ValueError('template must be text')
    def replace(match):
        key,default=match.groups()
        if key in environment: return environment[key]
        if default is not None: return default
        raise ValueError('missing environment value: '+key)
    # Check template syntax before substitution, so literal environment values
    # containing dollar signs are not reinterpreted as new templates.
    if '${' in _PATTERN.sub('',value): raise ValueError('malformed placeholder')
    return _PATTERN.sub(replace,value)

def plan(manifest,environment):
    if not isinstance(environment,dict) or any(not isinstance(k,str) or not isinstance(v,str) for k,v in environment.items()): raise ValueError('invalid environment')
    if not isinstance(manifest,dict) or set(manifest)!={'services'} or not isinstance(manifest['services'],dict): raise ValueError('invalid manifest')
    services=manifest['services']
    if any(not isinstance(name,str) or not name for name in services): raise ValueError('invalid name')
    resolved={}; ports_seen=set(); indegree={}; followers={name:[] for name in services}
    for name,service in services.items():
        if not isinstance(service,dict) or set(service)-{'depends_on','ports','env'}: raise ValueError('invalid service')
        deps=service.get('depends_on',[]); ports=service.get('ports',[]); env=service.get('env',{})
        if not isinstance(deps,list) or any(not isinstance(dep,str) for dep in deps) or len(set(deps))!=len(deps) or any(dep not in services for dep in deps): raise ValueError('invalid dependencies')
        if not isinstance(ports,list) or any(type(port) is not int or not 1<=port<=65535 for port in ports): raise ValueError('invalid ports')
        for port in ports:
            if port in ports_seen: raise ValueError('port collision')
            ports_seen.add(port)
        if not isinstance(env,dict) or any(not isinstance(key,str) for key in env): raise ValueError('invalid service environment')
        resolved[name]={'name':name,'depends_on':sorted(deps),'ports':sorted(ports),'env':{key:_resolve(value,environment) for key,value in env.items()}}
        indegree[name]=len(deps)
        for dep in deps: followers[dep].append(name)
    ready=[name for name,count in indegree.items() if not count]; heapq.heapify(ready); result=[]
    while ready:
        name=heapq.heappop(ready); result.append(resolved[name])
        for child in followers[name]:
            indegree[child]-=1
            if not indegree[child]: heapq.heappush(ready,child)
    if len(result)!=len(services): raise ValueError('dependency cycle')
    return result

def write_plan(manifest,environment,path):
    result=plan(manifest,environment); path=Path(path)
    fd,temporary=tempfile.mkstemp(dir=path.parent,prefix='.deployment-')
    try:
        with os.fdopen(fd,'w') as file:
            json.dump(result,file,sort_keys=True); file.write('\n'); file.flush(); os.fsync(file.fileno())
        os.replace(temporary,path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)
    return result
