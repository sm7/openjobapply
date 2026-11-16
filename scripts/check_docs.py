#!/usr/bin/env python3
"""Check that the documents in docs/ still match the project.

    python3 scripts/check_docs.py              # check every document in docs/
    python3 scripts/check_docs.py docs/x.md    # check named documents

It reports a problem when a document:

    1. cites a file path in backticks that does not exist,
    2. links to another document that does not exist,
    3. names a Python test method in backticks that does not exist in test/,
    4. contains a personal detail from profile.json: name, email, phone, street, zip or LinkedIn address.

One narrow exception applies to rule 4. The first name may appear inside a launchd label such as
com.NAME.applications, or as the value of the service_name setting. That is an operational identifier that an
operator needs to run launchctl. The same word anywhere else in a document is still a problem.

Paths under data/ and logs/ are runtime files. They may not exist on a fresh clone, so they are not checked.
A statement of a count is a dated snapshot and is never checked. The script reads files only and changes nothing.
Exit status is 1 when a problem is found, so a build can gate on it.
"""
import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent
PATH_PATTERN = re.compile(r"`((?:src|scripts|test|docs)/[A-Za-z0-9_./*-]+\.[a-z]{1,4}|jobapp\.py|profile\.json)`")
LINK_PATTERN = re.compile(r"\]\(([A-Za-z0-9_./-]+\.md)(?:#[^)]*)?\)")
TEST_PATTERN = re.compile(r"`(test_[A-Za-z0-9_]+)`")


def test_names():
    names = set()
    for path in (ROOT / "test").rglob("*"):
        if path.is_file() and path.suffix in (".py", ".js"):
            names |= set(re.findall(r"def (test_[A-Za-z0-9_]+)\(", path.read_text()))
    return names


def personal_details():
    """Values from profile.json that must never appear in a document. The file is read, never printed."""
    try:
        profile = json.loads((ROOT / "profile.json").read_text())
    except (OSError, ValueError):
        return []
    candidate = profile.get("candidate", {})
    keys = ("name", "first_name", "last_name", "email", "phone", "street", "zip", "linkedin_url")
    return [candidate[key] for key in keys if isinstance(candidate.get(key), str) and len(candidate[key]) >= 4]


def service_name():
    try:
        return json.loads((ROOT / "data/campaign.json").read_text())["runtime"]["service_name"]
    except (OSError, ValueError, KeyError):
        return None


def without_service_name(line, name):
    """Remove the allowed forms of the service name so that any other use of the same word is still found."""
    if not name:
        return line
    escaped = re.escape(name)
    line = re.sub(rf"com\.{escaped}\.", "com.SERVICE.", line, flags=re.I)
    return re.sub(rf"((?:service_name|value is)[^\n`]*`){escaped}(`)", r"\1SERVICE\2", line, flags=re.I)


def check(path, tests, secrets, name):
    problems = []
    for number, line in enumerate(path.read_text().splitlines(), 1):
        for cited in PATH_PATTERN.findall(line):
            if "*" not in cited and cited != "profile.json" and not (ROOT / cited).exists():  # profile.json is created by the user from profile.example.json
                problems.append((number, f"cited path does not exist: {cited}"))
        for target in LINK_PATTERN.findall(line):
            if not (path.parent / target).resolve().exists():
                problems.append((number, f"linked document does not exist: {target}"))
        for test in TEST_PATTERN.findall(line):
            used_as_prefix = re.search(r"\b(?:start|starts|begin|begins|prefix)\b", line, re.I)
            if test not in tests and not used_as_prefix and not any(known.startswith(test) for known in tests):
                problems.append((number, f"named test does not exist: {test}"))
        visible = without_service_name(line, name).lower()
        if any(secret.lower() in visible for secret in secrets):
            problems.append((number, "a personal detail from profile.json appears in this document"))
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="*")
    args = parser.parse_args(argv)
    files = [Path(name) for name in args.files] or sorted((ROOT / "docs").glob("*.md"))
    tests, secrets, name = test_names(), personal_details(), service_name()
    total = 0
    for path in files:
        for number, message in check(path, tests, secrets, name):
            print(f"{path}:{number}: {message}")
            total += 1
    print(f"\n{total} problems in {len(files)} document(s).")
    return 1 if total else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        sys.stderr.close()
        sys.exit(1)
