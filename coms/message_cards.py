"""Short mail cards, with fail-safe classification for old or malformed originals."""
import os
import re
import sys


TYPES = ("ASK", "RULING", "BLOCKER", "DONE", "FYI")
FIELDS = ("TYPE", "TOPIC", "NEED", "PROOF")
MAX_WORD = 40
QUOTED = re.compile(r'''(?<!\w)(?:"[^"\n]*"|'[^'\n]*')(?!\w)''')


def exempt(token):
    value = token.strip("(),;<>\"'.:")
    return ("://" in value or "/" in value or "\\" in value
            or re.fullmatch(r"(?:sha256:)?[a-fA-F0-9]{40,}", value, re.I) is not None)


def plain(line):
    # Preserve quoted owner wording and machine identifiers exactly, including spacing.
    outside = QUOTED.sub("quote", line)
    if (any(ord(c) < 32 or c.isspace() and c != " " for c in outside)
            or "  " in outside or outside != outside.strip()):
        raise ValueError("message card: use normal single spacing and no tabs in plain lines")
    for token in outside.split():
        if len(token) > MAX_WORD and not exempt(token):
            raise ValueError("message card: add spaces to words over 40 characters; paths, URLs, hashes and quoted owner words are exempt")


def parse(text):
    lines = text.splitlines()
    fields = {}
    for index, name in enumerate(FIELDS):
        if len(lines) <= index or not lines[index].startswith(name + ": "):
            raise ValueError("message card: add " + name + ": <value> in TYPE, TOPIC, NEED, PROOF order")
        value = lines[index][len(name) + 2:]
        if not value.strip():
            raise ValueError("message card: give " + name + " a value")
        fields[name] = value
    if fields["TYPE"] not in TYPES:
        raise ValueError("message card: TYPE must be ASK, RULING, BLOCKER, DONE or FYI")
    if (fields["NEED"].strip().lower() != "none"
            and fields["TYPE"] not in ("ASK", "BLOCKER")):
        raise ValueError("message card: a NEED other than none requires TYPE ASK or BLOCKER")
    proof = fields["PROOF"]
    if (any(c.isspace() for c in proof) or "://" in proof
            or not ("/" in proof or "\\" in proof or "." in proof)):
        raise ValueError("message card: PROOF must name one path containing '/' or '.' (or a Windows separator), not several paths or a URL")
    if len(lines[4:]) > 5:
        raise ValueError("message card: shorten the body to at most 5 body lines; put details in PROOF")
    for line in lines[:3] + lines[4:]:
        plain(line)
    return fields


def validate_action(text):
    # Format errors cannot hide an actionable NEED behind a missing header.
    headers = []
    for index, line in enumerate(text.splitlines()):
        line = line.lstrip()
        if index >= 3 and re.match(r"^PROOF[ \t]*:", line, re.I):
            break
        headers.append(line)
    header_text = "\n".join(headers)
    kinds = [value.strip() for value in re.findall(r"^TYPE[ \t]*:[ \t]*(.*)$", header_text, re.M | re.I)
             if value.strip()]
    needs = [value.strip() for value in re.findall(r"^NEED[ \t]*:[ \t]*(.*)$", header_text, re.M | re.I)
             if value.strip()]
    if any(kind not in TYPES for kind in kinds):
        raise ValueError("message card: TYPE must be ASK, RULING, BLOCKER, DONE or FYI")
    if (any(need.lower() != "none" for need in needs)
            and (not kinds or any(kind not in ("ASK", "BLOCKER") for kind in kinds))):
        raise ValueError("message card: a NEED other than none requires TYPE ASK or BLOCKER")


def validate_send(text):
    # Transition is opt-in; only a first-line TYPE: opts a legacy writer into cards.
    first = text.splitlines()[0] if text.splitlines() else ""
    if (os.environ.get("COMS_MESSAGE_CARDS") == "1"
            or first.startswith("TYPE:")):
        try:
            validate_action(text)
            parse(text)
        except ValueError as exc:
            print(f"coms: warning: {exc}; sending original text", file=sys.stderr)
    return text


def message_type(message):
    """Only a complete valid card has a type; old or malformed mail stays legacy."""
    try:
        return parse(message["text"])["TYPE"]
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def wakes(message):
    # Never trust a partial TYPE: FYI header or future metadata to silence legacy mail.
    return message_type(message) != "FYI"


def any_wakes(rows):
    return any(wakes(row) for row in rows)
