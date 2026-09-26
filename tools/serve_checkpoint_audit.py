"""Serve the actual dashboard against one audit fixture with isolated UI storage."""
import argparse
import os
from pathlib import Path
import tempfile
from .dashboard.agent_console import Console, Handler, ThreadingHTTPServer


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('workspace', type=Path)
    parser.add_argument('--port', type=int, default=8874)
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='checkpoint-dashboard-') as directory:
        root=Path(directory)
        os.environ['AUTOCODE_HOME']=str(root/'registry')
        console=Console([args.workspace],Path(__file__).with_name('autocode.py'),
            conversation_root=root/'conversations',project_store_root=root/'dashboard')
        server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
        server.console=console;server.hosts={f'127.0.0.1:{args.port}'}
        print(f'http://127.0.0.1:{args.port}',flush=True)
        try: server.serve_forever()
        finally: server.server_close()
