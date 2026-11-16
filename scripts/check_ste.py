#!/usr/bin/env python3
"""Check Markdown documents against the mechanical ASD-STE100 rules this project follows.

    python3 scripts/check_ste.py docs/*.md
    python3 scripts/check_ste.py --summary docs/*.md

It checks only what a program can check honestly:

    1. Active voice only                        heuristic: a form of "be" followed by a past participle
    2. Procedural sentences of 20 words or fewer  a sentence that starts with an imperative verb
    3. Descriptive sentences of 25 words or fewer every other sentence
    5. No "-ing" gerunds or participles          any word that ends in "ing" and is not on the allowlist
    7. Imperative mood for commands              heuristic: "you should", "you must", "you can" are refused
    8. Vertical lists for steps and items        a sentence with three or more comma-separated items
    9. No semicolons and no parentheses          any ";" or "(" outside code

It does NOT check rule 4 (one idea per sentence) or rule 6 (one approved meaning per word). Those need a human
reader and the official STE dictionary. A clean run is therefore necessary but not sufficient, and it is not a
certification of conformance to ASD-STE100.

Code blocks, inline code, tables, headings, links and front matter are literal text. They are not sentences.
Exit status is 1 when any violation is found, so the script can gate a build.
"""
import argparse
from pathlib import Path
import re
import sys

PROCEDURAL_LIMIT = 20
DESCRIPTIVE_LIMIT = 25

# Words that end in "ing" but are not gerunds or participles: nouns, names and fixed technical terms.
ING_ALLOWED = frozenset("""
thing things nothing anything something everything string strings spring king ring bring brings morning evening
during ceiling sibling shilling pudding wing swing sing sting ping pings ding ling bling cling fling sling
""".split())

# Words after which an "-ing" form is a noun in this project, such as the file "logging" or the module "pacing".
# Keep this list short. Rewrite the sentence when a word is not clearly a noun.
ING_PROJECT_NOUNS = frozenset("""
pacing routing scoring ranking setting settings mapping heading headings listing listings
""".split())

IMPERATIVE_STARTERS = frozenset("""
add apply ask attach avoid back build call change check choose clear click close collect configure confirm connect
copy create decline delete disable do edit enable ensure enter execute export fetch fill find fix follow generate
get give grant handle ignore import include insert install keep leave let list load make mark merge move name
note open pass paste press print provide put read record refer remove replace report request require reserve reset
resolve restart restore retry return review run save search select send set show sign start stop store submit
supply take tell test treat try turn type unload update upload use verify view wait write
""".split())

PASSIVE = re.compile(
    r"\b(?:is|are|was|were|be|been|being)\s+(?:not\s+)?(?:\w+ly\s+)?"
    r"(?!(?:able|about|all|also|any|only|not|no|one|none|set|that|this|these|those|the|a|an|at|in|on|to|for|of|by|with|"
    r"as|if|or|and|but|when|where|what|which|who|how|why|here|there|then|than|so|up|down|out|off|over|under|more|most|"
    r"less|least|first|last|next|same|other|each|every|both|either|neither|such|own|few|many|much|some|good|bad|new|old|"
    r"high|low|long|short|big|small|large|early|late|ready|safe|sure|true|false|empty|full|open|closed|live|alive|"
    r"active|idle|stale|fresh|clean|dirty|valid|invalid|real|fake|correct|wrong|right|left|done|gone|free|busy|due|"
    r"likely|unlikely|possible|impossible|necessary|different|similar|equal|important|available|unavailable|"
    r"present|absent|required|optional|allowed|enabled|disabled|unknown|known|needed|enough)\b)"
    r"\w+(?:ed|en)\b", re.I)
PASSIVE_PAST_IRREGULAR = re.compile(
    r"\b(?:is|are|was|were|be|been)\s+(?:not\s+)?(?:\w+ly\s+)?"
    r"(?:made|done|given|taken|shown|seen|known|written|sent|built|kept|held|found|left|lost|read|run|put|set|got|"
    r"chosen|broken|driven|hidden|stolen|spoken|thrown|worn|woven|drawn|grown|blown|flown|torn|born|begun|"
    r"stored|saved|queued|logged|marked|listed|called|used|added|opened|closed|started|stopped|checked)\b", re.I)
MODAL_TO_USER = re.compile(r"\byou\s+(?:should|must|can|may|could|would|will\s+need\s+to|need\s+to|have\s+to)\b", re.I)
ING_WORD = re.compile(r"\b([A-Za-z]+ing)\b")
LIST_OF_THREE = re.compile(r"[^,;:]+,\s*[^,;:]+,\s*(?:and\s+|or\s+)?[^,;:]+")


def strip_code(text):
    """Return the prose of a Markdown document with literal blocks removed, as (line_number, paragraph) pairs."""
    lines = text.splitlines()
    result, buffer, start = [], [], None
    in_fence = False
    in_front = lines[:1] == ["---"]
    for number, raw in enumerate(lines, 1):
        line = raw.rstrip()
        if in_front:
            if number > 1 and line == "---":
                in_front = False
            continue
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            if buffer:
                result.append((start, " ".join(buffer)))
                buffer, start = [], None
            continue
        literal = (in_fence or line.lstrip().startswith(("|", "#", ">"))
                   or re.fullmatch(r"\s*[-*_]{3,}\s*", line) is not None)
        if literal or not line.strip():
            if buffer:
                result.append((start, " ".join(buffer)))
                buffer, start = [], None
            continue
        item = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", line)
        if item != line:                               # a list item always starts a new paragraph
            if buffer:
                result.append((start, " ".join(buffer)))
            buffer, start = [item.strip()], number
            continue
        if not buffer:
            start = number
        buffer.append(line.strip())
    if buffer:
        result.append((start, " ".join(buffer)))
    return result


def clean_inline(paragraph):
    """Replace inline code and link targets with a neutral token so they do not count as prose."""
    paragraph = re.sub(r"`[^`]*`", "CODE", paragraph)
    paragraph = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", paragraph)
    paragraph = re.sub(r"https?://\S+", "URL", paragraph)
    paragraph = re.sub(r"[*_]{1,3}([^*_]+)[*_]{1,3}", r"\1", paragraph)
    return paragraph


def sentences(paragraph):
    """Split on sentence-ending punctuation followed by a capital letter, a digit, or the end."""
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])", paragraph.strip())
    return [part.strip() for part in parts if part.strip()]


def words(sentence):
    return re.findall(r"[A-Za-z0-9][A-Za-z0-9'./:_-]*", sentence)


def is_procedural(sentence):
    first = re.sub(r"[^a-z]", "", (sentence.split() or [""])[0].lower())
    return first in IMPERATIVE_STARTERS


def check_sentence(sentence):
    problems = []
    count = len(words(sentence))
    limit = PROCEDURAL_LIMIT if is_procedural(sentence) else DESCRIPTIVE_LIMIT
    kind = "procedural" if limit == PROCEDURAL_LIMIT else "descriptive"
    if count > limit:
        problems.append(f"{kind} sentence has {count} words, limit {limit}")
    if ";" in sentence:
        problems.append("semicolon")
    if "(" in sentence or ")" in sentence:
        problems.append("parenthesis")
    for word in ING_WORD.findall(sentence):
        lowered = word.lower()
        if lowered in ING_ALLOWED or lowered in ING_PROJECT_NOUNS or word.isupper():
            continue
        problems.append(f'"-ing" word "{word}"')
    if PASSIVE.search(sentence) or PASSIVE_PAST_IRREGULAR.search(sentence):
        problems.append("possible passive voice")
    if MODAL_TO_USER.search(sentence):
        problems.append('address to "you" with a modal verb: use the imperative')
    if LIST_OF_THREE.search(re.sub(r"\band\b", ",", sentence)) and sentence.count(",") >= 2 and count > 8:
        problems.append("three or more items in a sentence: use a vertical list")
    return problems


def check_file(path):
    findings = []
    for line_number, paragraph in strip_code(Path(path).read_text()):
        for sentence in sentences(clean_inline(paragraph)):
            for problem in check_sentence(sentence):
                findings.append((line_number, problem, sentence))
    return findings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="+")
    parser.add_argument("--summary", action="store_true", help="print counts only")
    args = parser.parse_args(argv)
    total = 0
    for name in args.files:
        findings = check_file(name)
        total += len(findings)
        if args.summary:
            kinds = {}
            for _, problem, _ in findings:
                key = re.sub(r"\d+ words, limit \d+", "too long", re.sub(r'"[^"]*"', '"..."', problem))
                kinds[key] = kinds.get(key, 0) + 1
            print(f"{name}: {len(findings)} violations", dict(sorted(kinds.items(), key=lambda kv: -kv[1])))
        else:
            for line_number, problem, sentence in findings:
                print(f"{name}:{line_number}: {problem}\n    {sentence[:160]}")
    print(f"\n{total} violations in {len(args.files)} file(s).")
    return 1 if total else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        # The reader (for example `head`) closed the pipe. This is not an error in the checker.
        sys.stderr.close()
        sys.exit(1)
