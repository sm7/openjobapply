#!/usr/bin/env python3
"""Fail when a file that could be published holds personal data or a secret.

Run from the project root:  python3 scripts/check_privacy.py [--denylist FILE] [PATH ...]

Built-in checks (no personal data is stored in this script):
  - an email address outside the reserved test domains (example.*, *.invalid, *.test, localhost)
  - a formatted US phone number that is not in the reserved fictional range 555-0100 to 555-0199
  - an absolute home path such as /Users/<name>/ or /home/<name>/
  - a private key block or a token that looks like a cloud or Git hosting secret

Optional local checks: put one case-insensitive literal per line in `.privacy-denylist`
(gitignored) or pass --denylist FILE. Use it for your own name, address and employers.
Lines that start with # are comments. A line that contains the marker `privacy-ok` is skipped.
Exit status 0 means clean, 1 means findings.
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {'.git', '__pycache__', 'node_modules', '.venv', 'data', 'logs', 'vendor'}
SKIP_FILES = {'profile.json', '.privacy-denylist'}
BINARY_SUFFIXES = {'.pdf', '.png', '.jpg', '.jpeg', '.gif', '.webp', '.ico', '.db', '.pyc'}

EMAIL = re.compile(r'[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)')
PHONE = re.compile(r'(?<![\w.-])(?:\+?1[-. ]?)?\(?([2-9]\d{2})\)?[-. ]?([2-9]\d{2})[-. ]?(\d{4})(?!\d)')
HOME_PATH = re.compile(r'(?:/Users|/home)/(?!(?:you|user|name|username|example|runner|me)\b)[A-Za-z0-9._-]+/')
SECRET = re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----|\bAKIA[0-9A-Z]{16}\b|\bghp_[A-Za-z0-9]{30,}\b|\bsk-[A-Za-z0-9]{32,}\b')
ALLOWED_EMAIL_DOMAIN = re.compile(r'(^|\.)(example\.(com|org|net)|invalid|test|localhost|example|text\.text)$', re.I)


def candidate_files(paths):
    if paths:
        for item in paths:
            path = Path(item)
            yield from ([path] if path.is_file() else sorted(p for p in path.rglob('*') if p.is_file()))
        return
    try:
        listed = subprocess.run(['git', 'ls-files', '--cached', '--others', '--exclude-standard'], cwd=ROOT,
                                capture_output=True, text=True, check=True).stdout.splitlines()
        for name in listed:
            if (ROOT / name).is_file():
                yield ROOT / name
    except (OSError, subprocess.CalledProcessError):
        yield from sorted(p for p in ROOT.rglob('*') if p.is_file())


def skipped(path):
    try:
        parts = set(path.resolve().relative_to(ROOT).parts)
    except ValueError:
        parts = set(path.parts)
    return bool(parts & SKIP_DIRS) or path.name in SKIP_FILES or path.suffix.lower() in BINARY_SUFFIXES


def load_denylist(option):
    path = Path(option) if option else ROOT / '.privacy-denylist'
    if not path.is_file():
        return []
    return [line.strip().lower() for line in path.read_text().splitlines() if line.strip() and not line.startswith('#')]


def scan_text(name, text, denylist):
    findings = []
    for number, line in enumerate(text.splitlines(), 1):
        lowered = line.lower()
        if 'privacy-ok' in lowered:
            continue
        for match in EMAIL.finditer(line):
            if not ALLOWED_EMAIL_DOMAIN.search(match.group(1)):
                findings.append((name, number, 'email address', match.group(0)))
        for match in PHONE.finditer(line):
            area, exchange, line_number = match.groups()
            if re.fullmatch(r'\+?\d+', match.group(0).strip()):
                continue  # bare digit runs are usually job or record ids, not phone numbers
            if not (exchange == '555' and line_number.startswith('01')):
                findings.append((name, number, 'phone number outside 555-0100..0199', match.group(0).strip()))
        for match in HOME_PATH.finditer(line):
            findings.append((name, number, 'absolute home path', match.group(0)))
        for match in SECRET.finditer(line):
            findings.append((name, number, 'secret-like value', match.group(0)[:12] + '...'))
        for literal in denylist:
            if literal in lowered:
                findings.append((name, number, 'denylisted text', literal))
    return findings


def scan(paths=None, denylist=()):
    findings = []
    for path in candidate_files(paths):
        if skipped(path):
            continue
        try:
            text = path.read_text(encoding='utf-8')
        except (UnicodeDecodeError, OSError):
            continue
        try:
            name = str(path.resolve().relative_to(ROOT))
        except ValueError:
            name = str(path)
        findings.extend(scan_text(name, text, denylist))
    return findings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('paths', nargs='*')
    parser.add_argument('--denylist', help='file with one forbidden literal per line')
    args = parser.parse_args(argv)
    findings = scan(args.paths, load_denylist(args.denylist))
    for name, number, kind, value in findings:
        print(f'{name}:{number}: {kind}: {value}')
    print(f'{len(findings)} finding(s)')
    return 1 if findings else 0


if __name__ == '__main__':
    sys.exit(main())
