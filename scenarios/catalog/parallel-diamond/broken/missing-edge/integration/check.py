from client.fetch import fetch
from server.handler import handle


def check():
    return f"{handle('D')} / {fetch('D')}"
