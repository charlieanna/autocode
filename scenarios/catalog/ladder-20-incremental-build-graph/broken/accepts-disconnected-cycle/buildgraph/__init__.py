import hashlib
import heapq
import json
import os
import tempfile
from pathlib import Path

def topology(graph):
    indegree={}; followers={node:[] for node in graph}
    for node,deps in graph.items():
        if len(deps)!=len(set(deps)) or any(dep not in graph for dep in deps): raise ValueError('invalid dependency')
        indegree[node]=len(deps)
        for dep in deps: followers[dep].append(node)
    ready=[node for node,count in indegree.items() if not count]; heapq.heapify(ready); result=[]
    while ready:
        node=heapq.heappop(ready); result.append(node)
        for follower in followers[node]:
            indegree[follower]-=1
            if not indegree[follower]: heapq.heappush(ready,follower)
    # Wrong: returns the acyclic prefix.
    return result

class Builder:
    def __init__(self,graph,cache_path):
        self.graph={node:list(deps) for node,deps in graph.items()}
        self.path=Path(cache_path)
    def build(self,sources,compile_fn):
        order=topology(self.graph)
        if set(sources)!=set(self.graph) or any(not isinstance(s,str) for s in sources.values()): raise ValueError('invalid sources')
        cache={}
        if self.path.exists():
            try:
                cache=json.loads(self.path.read_text())
                if not isinstance(cache,dict) or any(not isinstance(v,dict) or set(v)!={'fingerprint','output'} or not all(isinstance(x,str) for x in v.values()) for v in cache.values()): raise ValueError('invalid cache')
            except (ValueError,UnicodeError) as error: raise ValueError('invalid cache') from error
        outputs={}; next_cache={}
        for node in order:
            deps={dep:outputs[dep] for dep in self.graph[node]}
            fingerprint=hashlib.sha256(json.dumps([sources[node],self.graph[node],deps],sort_keys=True).encode()).hexdigest()
            prior=cache.get(node,{})
            if prior.get('fingerprint')==fingerprint: output=prior['output']
            else: output=compile_fn(node,sources[node],dict(deps))
            if not isinstance(output,str): raise ValueError('compiler output must be text')
            outputs[node]=output; next_cache[node]={'fingerprint':fingerprint,'output':output}
        fd,temporary=tempfile.mkstemp(dir=self.path.parent,prefix='.build-')
        try:
            with os.fdopen(fd,'w') as file:
                json.dump(next_cache,file,sort_keys=True); file.flush(); os.fsync(file.fileno())
            os.replace(temporary,self.path)
        finally:
            if os.path.exists(temporary): os.unlink(temporary)
        return outputs
