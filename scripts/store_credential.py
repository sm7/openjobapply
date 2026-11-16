#!/usr/bin/env python3
"""Store ONE employer-site password in the credential vault from a hidden prompt.

    python3 scripts/store_credential.py <site> <email>
    e.g. python3 scripts/store_credential.py jobs.example.com jane.doe@example.invalid

The password is read with echo OFF (getpass). It is never a command-line argument (so it is not in shell
history or `ps`), never printed, never logged. It is written only to the owner-only vault file used by
src/ats/credentials.js (same path, same JSON shape, same site-key rule). The audit log records site + email only.
"""
import datetime
import getpass
import json
import os
import re
import sys
import tempfile
from urllib.parse import urlparse

FILE = os.environ.get("ATS_CREDENTIALS_FILE") or os.path.join(os.path.expanduser("~"), ".omp", "openjobapply", "credentials.json")
AUDIT = os.path.join(os.path.dirname(FILE), "credentials_audit.log")


def site_key(value):
    """Mirror of siteKey() in src/ats/credentials.js."""
    host = str(value or "").lower()
    try:
        host = urlparse(host if re.match(r"^[a-z]+://", host) else "https://" + host).hostname or host
    except ValueError:
        pass
    host = re.sub(r"^www\.", "", host)
    m = re.match(r"^([^.]+)\.(?:wd\d+\.)?(myworkdayjobs|myworkdaysite)\.com$", host)
    if m:
        return f"{m.group(1)}.{m.group(2)}.com"
    m = re.match(r"^(?:jobs-|careers-)?([^.]+)\.icims\.com$", host)
    if m:
        return f"{m.group(1)}.icims.com"
    return ".".join(host.split(".")[-2:])


def read():
    if not os.path.exists(FILE):
        return {"version": 1, "entries": []}
    with open(FILE) as f:
        data = json.load(f)  # corrupt file raises: never silently overwrite credentials
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        sys.exit("credentials.json is malformed; refusing to overwrite")
    return data


def write(data):
    os.makedirs(os.path.dirname(FILE), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(FILE), suffix=".tmp")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp, FILE)
        os.chmod(FILE, 0o600)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def main():
    if len(sys.argv) != 3:
        sys.exit("usage: python3 scripts/store_credential.py <site> <email>")
    site, email = sys.argv[1], sys.argv[2]
    if not sys.stdin.isatty():
        sys.exit("refusing: stdin is not a terminal, so the password could not be hidden. Run this in Terminal.")
    key = site_key(site)
    data = read()
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    entry = next((e for e in data["entries"] if e.get("site") == key and e.get("email", "").lower() == email.lower()), None)
    if entry:
        print(f"A credential for {key} / {email} already exists and will be replaced.")
    a = getpass.getpass(f"Password for {key} ({email}): ")
    b = getpass.getpass("Type it again to confirm: ")
    if not a or a != b:
        sys.exit("Empty or the two entries differ. Nothing was stored.")
    if entry:
        entry.update({"password": a, "updatedAt": now, "origin": "user"})
    else:
        data["entries"].append({"site": key, "email": email, "password": a, "createdAt": now, "updatedAt": now, "origin": "user", "resets": 0})
    write(data)
    with open(AUDIT, "a") as f:
        f.write(json.dumps({"t": now, "event": "credential_saved", "site": key, "email": email}, separators=(",", ":")) + "\n")
    print(f"Stored. {key} / {email} is now in the vault. The password was not printed or logged.")


if __name__ == "__main__":
    main()
