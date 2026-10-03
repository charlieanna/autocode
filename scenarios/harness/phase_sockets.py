"""Loopback egress guard for a single acceptance child's full lifecycle.

Catches proxy connections and dependency transports outside HTTPX without
changing listener/accept or local IPC behavior. Not a hostile-code sandbox.
"""
from contextlib import contextmanager
import socket
from .phase_env import guard


@contextmanager
def guard_connections(env=None):
    policy = guard(env)
    original_dns = socket.getaddrinfo
    original_connect, original_connect_ex = socket.socket.connect, socket.socket.connect_ex

    def check(host, port):
        if isinstance(host, bytes):
            host = host.decode('ascii')
        host = str(host)
        if ':' in host:
            host = '[' + host + ']'
        policy.check('CONNECT', f'http://{host}:{port}')

    def getaddrinfo(host, port, *args, **kwargs):
        if host is not None:
            check(host, port)
        return original_dns(host, port, *args, **kwargs)

    def connect(connection, address):
        if connection.family in (socket.AF_INET, socket.AF_INET6):
            check(*address[:2])
        return original_connect(connection, address)

    def connect_ex(connection, address):
        if connection.family in (socket.AF_INET, socket.AF_INET6):
            check(*address[:2])
        return original_connect_ex(connection, address)

    try:
        socket.getaddrinfo = getaddrinfo
        socket.socket.connect, socket.socket.connect_ex = connect, connect_ex
        yield policy
    finally:
        socket.getaddrinfo = original_dns
        socket.socket.connect, socket.socket.connect_ex = original_connect, original_connect_ex
