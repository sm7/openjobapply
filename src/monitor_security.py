"""Protection for the dashboard's state-changing requests.

The dashboard is bound to loopback, but that alone does not stop a web page in the user's browser, or another
local program, from posting to it. A write is accepted only when all of these hold: the Host is this
dashboard's own address (defeats DNS rebinding), any Origin is the dashboard itself (defeats cross-site
posts), the body is JSON (forces a CORS preflight), and the caller presents the per-install secret.

No database, HTML or domain logic lives here.
"""
import hmac
import os
from pathlib import Path
import secrets

import db

TOKEN_HEADER = 'X-Monitor-Token'
TOKEN_FILE = db.ROOT / 'data/monitor-token'
MAX_BODY_BYTES = 64 * 1024


class Rejected(Exception):
    """A write request failed protection. ``status`` is the HTTP status to return."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def load_token(path=None):
    """Return the per-install secret, creating it on first use with owner-only permissions.

    Creation is race-safe, so the foreground dashboard and an installed service never disagree about it.
    """
    path = Path(path or TOKEN_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        token = path.read_text().strip()
        if not token:
            raise RuntimeError('dashboard token file is empty; delete it to create a new one') from None
        return token
    token = secrets.token_urlsafe(32)
    with os.fdopen(descriptor, 'w') as output:
        output.write(token)
    return token


def _own_hosts(port):
    return {f'127.0.0.1:{port}', f'localhost:{port}'}


def check_host(headers, port):
    """Raise Rejected unless the request addressed this dashboard by its own loopback address.

    Every route calls this, reads included. A DNS-rebinding page reaches the dashboard under its own hostname,
    and without this check it could read the write token from the page and then use it.
    """
    if headers.get('Host', '') not in _own_hosts(port):
        raise Rejected(403, 'Unexpected host')


def check_write(headers, port, token):
    """Raise Rejected unless this request is allowed to change state. ``headers`` is a Message-like mapping."""
    check_host(headers, port)
    origin = headers.get('Origin')
    if origin is not None and origin not in {f'http://{host}' for host in _own_hosts(port)}:
        raise Rejected(403, 'Cross-site request refused')
    media_type = headers.get('Content-Type', '').split(';')[0].strip().lower()
    if media_type != 'application/json':
        raise Rejected(415, 'Content-Type must be application/json')
    presented = headers.get(TOKEN_HEADER, '')
    if not presented or not hmac.compare_digest(presented.encode(), token.encode()):
        raise Rejected(403, 'Missing or invalid dashboard token')


def read_body(headers, stream):
    """Read exactly Content-Length bytes, refusing a missing, malformed or oversized length."""
    try:
        length = int(headers.get('Content-Length', ''))
    except ValueError:
        raise Rejected(411, 'Content-Length is required') from None
    if length < 0:
        raise Rejected(400, 'Invalid Content-Length')
    if length > MAX_BODY_BYTES:
        raise Rejected(413, 'Request body too large')
    return stream.read(length)
