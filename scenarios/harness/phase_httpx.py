"""Opt-in default HTTPX transport observer for acceptance children.

Install before application imports/startup, keep it across collection, call and
teardown, then aggregate the phase refusal ledger. Mock/ASGI transports remain
local; genuine default transports must satisfy the existing loopback policy.
This context changes only its calling process, never a model-provider parent.
HTTPX is a project dependency, not an AutoCode runtime dependency.
"""
from contextlib import contextmanager
from .phase_env import guard


@contextmanager
def guard_default_httpx(env=None, *, httpx_module=None):
    """Observe default sync/async requests before transport I/O, including redirects.

    Use one context for the child's complete application/test lifecycle. The
    optional module argument permits testing the transport protocol without
    making HTTPX a dependency of the standard-library scenario harness.
    """
    if httpx_module is None:
        import httpx as httpx_module
    policy = guard(env)
    sync = httpx_module.HTTPTransport
    asynchronous = httpx_module.AsyncHTTPTransport
    original_sync, original_async = sync.handle_request, asynchronous.handle_async_request

    def handle_request(transport, request, *args, **kwargs):
        policy.check(request.method, str(request.url))
        return original_sync(transport, request, *args, **kwargs)

    async def handle_async_request(transport, request, *args, **kwargs):
        policy.check(request.method, str(request.url))
        return await original_async(transport, request, *args, **kwargs)

    sync.handle_request = handle_request
    asynchronous.handle_async_request = handle_async_request
    try:
        yield policy
    finally:
        sync.handle_request = original_sync
        asynchronous.handle_async_request = original_async
