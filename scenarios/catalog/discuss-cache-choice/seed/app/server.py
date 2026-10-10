"""WSGI application. Each request handler needs the TLD's metadata."""

from .metadata import metadata


def application(environ, start_response):
    tld = environ.get("PATH_INFO", "/").strip("/").split("/")[0] or "com"
    doc = metadata(tld)
    body = f"{tld}: grace {doc['grace_days']} days, max {doc['max_years']} years\n".encode()
    start_response("200 OK", [("Content-Type", "text/plain")])
    return [body]
