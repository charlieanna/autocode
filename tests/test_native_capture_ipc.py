"""Actual inherited-channel and kernel controls; no image acceptance authority."""
from pathlib import Path
import fcntl
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest

import autocode_native_capture_policy as capture_policy
import autocode_util as util


class NativeCaptureIPCTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node is required for native IPC controls')
    def test_original_unix_fd_refuses_foreign_identity_nonce_host_port_and_restores(self):
        node = shutil.which('node')
        self.assertIsNotNone(node, 'The controlled gate must bind its actual Node executable')
        module = Path(__file__).resolve().parents[1] / 'tools/autocode_native_capture_browser.cjs'
        sockets, facts = [], []
        for _ in range(2):
            parent, original = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
            child = socket.socket(fileno=fcntl.fcntl(original.fileno(), fcntl.F_DUPFD_CLOEXEC, 10))
            original.close()
            info = os.fstat(child.fileno())
            sockets.append((parent, child))
            facts.append({'fd': child.fileno(), 'device': info.st_dev, 'inode': info.st_ino, 'mode': info.st_mode})
        script = r'''
const assert=require('node:assert/strict'),fs=require('node:fs'),net=require('node:net');
const http=require('node:http'),os=require('node:os'),path=require('node:path');
const {install}=require(process.argv[1]),inputs=JSON.parse(process.argv[2]);
const originalConnect=net.connect,originalCreate=net.createConnection,originalListen=http.Server.prototype.listen;
const root=fs.mkdtempSync(path.join(os.tmpdir(),'native-ipc-adapter-'));
const fd=fs.openSync(path.join(root,'lease'),'wx'),info=fs.fstatSync(fd);
const nonce='original-controlled-nonce';
const unchanged=()=>{assert.equal(net.connect,originalConnect);assert.equal(net.createConnection,originalCreate);assert.equal(http.Server.prototype.listen,originalListen);};
try {
 for(const [index,alias] of ['connect','createConnection'].entries()) {
  const socket=inputs[index],observed=fs.fstatSync(socket.fd,{bigint:true});
  console.log('ORIGINAL_UNIX_STAT '+JSON.stringify({alias,expected:socket,actual:{device:String(observed.dev),inode:String(observed.ino),mode:String(observed.mode)}}));
  const config={native_transport:{nonce,cdp:{nonce,socket},listener:{fd,device:info.dev,inode:info.ino,
   mode:info.mode,address:['127.0.0.1',43210]},requests:root,responses:root},timeout_ms:1000,assets:[]};
  for(const change of [v=>v.cdp.nonce='foreign',v=>v.cdp.socket.inode++,
    v=>v.cdp.socket.fd=fd,v=>v.listener.inode++,v=>v.listener.address[0]='127.0.0.2',
    v=>v.cdp.socket.device+=4294967296,v=>v.cdp.socket.device-=4294967296,
    v=>v.cdp.socket.inode=Number.MAX_SAFE_INTEGER+1,v=>v.cdp.socket.mode+=65536]) {
   const bad=structuredClone(config);change(bad.native_transport);
   assert.throws(()=>install(bad),/Native capture:/);unchanged();
  }
  const api=install(config);
  try {
   for(const attempt of ['connect','createConnection'])
    for(const options of [{host:'127.0.0.2',port:80},{host:'localhost',port:80},{host:'127.0.0.1',port:81},
      {host:'127.0.0.1',port:80,path:'/tmp/foreign'},{host:'127.0.0.1',port:80,socketPath:'/tmp/foreign'},
      {host:'127.0.0.1',port:80,localAddress:'127.0.0.1'},{host:'127.0.0.1',port:80,localPort:80}])
     assert.throws(()=>net[attempt](options),/single inherited CDP/);
   const actual=net[alias]({host:'127.0.0.1',port:80});
   for(const attempt of ['connect','createConnection'])
    assert.throws(()=>net[attempt]({host:'127.0.0.1',port:80}),/single inherited CDP/);
   actual.end('original-channel-'+alias,()=>actual.destroy());
  } finally {api.restore();unchanged();}
 }
 console.log('ACTUAL_UNIX_ADAPTER_CONTROLS_PASS');
} finally {fs.closeSync(fd);fs.rmSync(root,{recursive:true,force:true});unchanged();}
'''
        try:
            result = subprocess.run([node, '-e', script, str(module), json.dumps(facts)],
                pass_fds=tuple(row['fd'] for row in facts), capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            for (parent, _child), alias in zip(sockets, ('connect', 'createConnection')):
                parent.settimeout(1)
                self.assertEqual(parent.recv(64), ('original-channel-'+alias).encode())
            lines = result.stdout.strip().splitlines()
            self.assertEqual(lines[-1], 'ACTUAL_UNIX_ADAPTER_CONTROLS_PASS')
            self.assertEqual(len(lines), 3)
            for line, expected in zip(lines[:2], facts):
                self.assertTrue(line.startswith('ORIGINAL_UNIX_STAT '))
                observed = json.loads(line.split(' ', 1)[1])
                self.assertEqual(observed['expected'], expected)
                print(line, flush=True)
        finally:
            for parent, child in sockets:
                parent.close()
                child.close()

    @unittest.skipUnless(sys.platform == 'darwin' and os.environ.get('AUTOCODE_NATIVE_CAPTURE_RUNTIME'),
                         'Darwin and an explicit exact-file native capture runtime are required')
    def test_original_browser_policy_denies_other_host_port_and_foreign_mach_service(self):
        manifest = os.environ.get('AUTOCODE_NATIVE_CAPTURE_RUNTIME')
        self.assertIsNotNone(manifest, 'Kernel gate requires the exact capture runtime manifest')
        retained = os.environ.get('AUTOCODE_NATIVE_IPC_TEST_ROOT')
        if retained:
            root = Path(retained).resolve()
            root.mkdir(mode=0o700, exist_ok=False)
        else:
            temporary = tempfile.TemporaryDirectory(prefix='native-ipc-kernel-')
            self.addCleanup(temporary.cleanup)
            root = Path(temporary.name).resolve()
        # Compile only this controlled syscall probe; supplied application bytes
        # are never compiled or executed by the trusted bootstrap.
        probe_source = root / 'kernel.c'
        probe_source.write_text(r'''
#include <arpa/inet.h>
#include <errno.h>
#include <mach/mach.h>
#include <servers/bootstrap.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/socket.h>
#include <unistd.h>
static int attempt(const char *ip, int port) {
 int fd=socket(AF_INET,SOCK_STREAM,0); if(fd<0)return errno;
 struct sockaddr_in a={0};a.sin_family=AF_INET;a.sin_port=htons(port);inet_pton(AF_INET,ip,&a.sin_addr);
 int result=connect(fd,(struct sockaddr*)&a,sizeof(a));int saved=result==0?0:errno;close(fd);return saved;
}
int main(int argc,char **argv) {
 if(argc!=4)return 2;
 char own[256];snprintf(own,sizeof(own),"org.chromium.Chromium.MachPortRendezvousServer.%d",getpid());
 mach_port_t server=MACH_PORT_NULL,found=MACH_PORT_NULL,foreign=MACH_PORT_NULL;
 kern_return_t registration=bootstrap_check_in(bootstrap_port,own,&server);
 kern_return_t lookup=bootstrap_look_up(bootstrap_port,own,&found);
 kern_return_t denied=bootstrap_look_up(bootstrap_port,argv[3],&foreign);
 int host=attempt("127.0.0.2",atoi(argv[1])),port=attempt("127.0.0.1",atoi(argv[2]));
 printf("{\"pid\":%d,\"own_service\":\"%s\",\"own_registration\":%d,\"own_lookup\":%d,\"foreign_lookup\":%d,\"wrong_host_errno\":%d,\"wrong_port_errno\":%d}\n",getpid(),own,registration,lookup,denied,host,port);
 return registration==KERN_SUCCESS && lookup==KERN_SUCCESS && denied==BOOTSTRAP_NOT_PRIVILEGED &&
  (host==EPERM||host==EACCES) && (port==EPERM||port==EACCES)?0:1;
}
''')
        executable = root / 'kernel-probe'
        compiled = subprocess.run(['/usr/bin/clang', '-Os', '-Wall', '-Werror', '-Wno-deprecated-declarations',
            str(probe_source), '-o', str(executable)], capture_output=True, text=True, timeout=60)
        (root / 'compile.stdout').write_text(compiled.stdout)
        (root / 'compile.stderr').write_text(compiled.stderr)
        self.assertEqual(compiled.returncode, 0, compiled.stderr)
        executable.chmod(0o500)
        workspace = root / 'workspace'
        workspace.mkdir(mode=0o700)
        private = workspace / '.autocode'
        private.mkdir(mode=0o700)
        run = private / 'controlled-run'
        run.mkdir(mode=0o700)
        runtime_path = private / 'capture-runtime.json'
        runtime_path.write_bytes(Path(manifest).read_bytes())
        runtime_path.chmod(0o400)
        runtime = json.loads(runtime_path.read_bytes())
        (workspace / 'page.html').write_text('<!doctype html><title>exact controlled source</title>')
        config = {'playwright_module': runtime['playwright_module'], 'executable': runtime['browser'],
            'assets': [{'url': '/', 'path': 'page.html'}]}
        profile = {'capture_runtime': {'path': '.autocode/capture-runtime.json', 'sha256': util.file_hash(runtime_path)}}
        snapshot = {'files': {'page.html': util.file_hash(workspace / 'page.html')}}
        boundary = capture_policy.CaptureBoundary(workspace, run, snapshot, config,
            'controlled-policy-only', {}, profile, timeout=120)
        listeners = []
        try:
            for address in ('127.0.0.1',):
                item = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                listeners.append(item)
                item.bind((address, 0))
                item.listen(1)
                item.setblocking(False)
            bootstrap = Path(capture_policy.__file__).with_name('autocode_capture_bootstrap.cjs')
            read = subprocess.run([runtime['node'], '-e',
                'process.stdout.write(require(process.argv[1]).BROWSER_EXEC)', str(bootstrap)],
                env=boundary.environment, capture_output=True, text=True, timeout=min(20,boundary.remaining()))
            self.assertEqual(read.returncode, 0, read.stderr)
            policy = boundary.policy() + '(allow file-read* (literal ' + json.dumps(str(executable)) + '))\n'
            (root / 'controlled-policy.sb').write_text(policy)
            foreign = 'org.chromium.Chromium.MachPortRendezvousServer.' + str(os.getpid())
            command = ['/bin/sh', '-c', read.stdout, 'native-owned-browser', policy, str(executable),
                str(boundary.broker.identity['address'][1]), str(listeners[0].getsockname()[1]), foreign]
            result = subprocess.run(command, cwd=boundary.tree, env=boundary.environment,
                capture_output=True, text=True, timeout=min(20,boundary.remaining()))
            (root / 'kernel.stdout').write_text(result.stdout)
            (root / 'kernel.stderr').write_text(result.stderr)
            (root / 'bindings.json').write_text(json.dumps({'executable_sha256':util.file_hash(executable),
                'source_sha256':util.file_hash(probe_source),'bootstrap_sha256':util.file_hash(bootstrap),
                'policy_sha256':util.file_hash(root/'controlled-policy.sb'),
                'foreign_service':foreign,'returncode':result.returncode,
                'approved_address':boundary.broker.identity['address'],
                'wrong_host':['127.0.0.2',boundary.broker.identity['address'][1]],
                'wrong_port':['127.0.0.1',listeners[0].getsockname()[1]]},indent=2)+'\n')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            actual = json.loads(result.stdout)
            self.assertEqual(actual['own_service'], 'org.chromium.Chromium.MachPortRendezvousServer.'+str(actual['pid']))
            self.assertNotEqual(actual['own_service'], foreign)
            self.assertEqual(actual['own_registration'], 0)
            self.assertEqual(actual['own_lookup'], 0)
            self.assertEqual(actual['foreign_lookup'], 1100)
            for listener in listeners:
                with self.assertRaises(BlockingIOError):
                    listener.accept()
            boundary.require_current()
        finally:
            boundary.close()
            for listener in listeners:
                listener.close()
