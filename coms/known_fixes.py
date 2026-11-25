"""Suggest catalogued fixes in a dedicated local COMS inbox."""

import hashlib
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import quote

from coms.identity import check, valid_holder
from coms.inbox import Inbox
from coms.message_cards import parse
from coms.mutex import FileMutex
from coms.store import Store

REFERENCE = "Reference: "


def words(text):
    return set(re.findall(r"[^\W_]+", text.casefold()))


def quoted(text):
    delimiter = "'" if '"' in text and "'" not in text else '"'
    return delimiter + text + delimiter


def ruling(topic, fix, tool, decision, proof="known-fixes.txt"):
    return (f"TYPE: RULING\nTOPIC: {topic}\nNEED: none\nPROOF: {proof}\n"
            f"Fix: {quoted(fix)}\nTool: {quoted(tool)}\nDecision: {quoted(decision)}")


def read_fixes(path, optional=False):
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        if optional:
            return []
        raise
    fixes = []
    for index, line in enumerate(lines, 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = [part.strip() for part in line.split("|")]
        try:
            if len(parts) != 4 or not all(parts) or not words(parts[0]):
                raise ValueError("expected symptom words, fix, tool and decision")
            # A catalog entry must produce a valid card before it can send anything.
            parse(ruling("Known fix", *parts[1:]))
        except ValueError:
            print(f"known-fixes: ignoring invalid catalog line {index}", file=sys.stderr)
            continue
        fixes.append((words(parts[0]), *parts[1:]))
    return sorted(fixes, key=lambda entry: -len(entry[0]))


def match_card(text, fixes, proof="known-fixes.txt"):
    try:
        card = parse(text)
    except ValueError:
        return None
    if card["TYPE"] not in ("ASK", "BLOCKER"):
        return None
    symptom = words(" ".join([card["TOPIC"], card["NEED"], *text.splitlines()[4:]]))
    for required, fix, tool, decision in fixes:
        if required <= symptom:
            return ruling(card["TOPIC"], fix, tool, decision, proof=proof)
    return None


def fingerprint(row):
    original = json.dumps([row["from"], row["ts"], row["text"]]).encode("ascii")
    return hashlib.sha256(original).hexdigest()


def proof_path(config, coms_dir):
    config, root = config.absolute(), Path(coms_dir).absolute()
    if config.is_relative_to(root):
        return "./" + quote(str(config.relative_to(root)), safe="/")
    return quote(str(config), safe="/")


def destination_refs(store, holder):
    # Rotation copies the live inbox first, so reading live before listing
    # archives sees a delivered reply even if rotation runs during this scan.
    state = Path(store.dir)
    archive = state / "inbox-archive"
    archived_name = re.compile(re.escape(holder) + r"-[0-9]{8}T[0-9]{6}Z-to-line[0-9]+\.jsonl")

    def paths():
        yield state / "inbox" / (holder + ".jsonl")
        if archive.is_dir():
            yield from (path for path in archive.iterdir() if archived_name.fullmatch(path.name))

    sent = set()
    for path in paths():
        if path.is_symlink():
            raise ValueError("unexpected link in recipient mail")
        try:
            with path.open("rb") as source:
                for line in source:
                    if not line.endswith(b"\n"):
                        raise ValueError("incomplete recipient mail, reply deferred")
                    if b"\\nReference: " not in line:
                        continue
                    try:
                        row = json.loads(line)
                    except (ValueError, RecursionError):
                        continue
                    if not isinstance(row, dict) or row.get("from") != store.holder_id:
                        continue
                    text = row.get("text")
                    if isinstance(text, str) and text.startswith("TYPE: RULING\n"):
                        last = text.rsplit("\n", 1)[-1]
                        if last.startswith(REFERENCE):
                            sent.add(last[len(REFERENCE):])
        except FileNotFoundError:
            continue
    return sent


def send_suggestions(store, fixes, targets, reply, proof):
    rows, _ = Inbox(store).snapshot(reset=True, initialize=False, review=True)
    matches = sent = failed = 0
    scanned = {}
    seen = set()
    for row in rows:
        sender = row["from"]
        if sender == store.holder_id or (targets and sender not in targets):
            continue
        reference = fingerprint(row)
        if reference in seen:
            continue
        seen.add(reference)
        response = match_card(row["text"], fixes, proof=proof)
        if response is None:
            continue
        try:
            if reply:
                if sender not in scanned:
                    scanned[sender] = destination_refs(store, sender)
                if reference in scanned[sender]:
                    continue
                store.send_message(sender, response + "\n" + REFERENCE + reference)
                sent += 1
            matches += 1
        except (OSError, ValueError):
            failed += 1
    print(f"known-fixes: matches={matches} replies={sent} errors={failed}")
    return bool(failed)


def run(args, coms_dir):
    holder = valid_holder(os.environ.get("COMS_HOLDER", ""))
    targets = {valid_holder(target) for target in args.reply_to}
    if args.reply and not targets:
        raise ValueError("known-fixes needs at least one explicitly allowed reply recipient")
    config = args.catalog or Path(coms_dir) / "known-fixes.txt"
    fixes = read_fixes(config, optional=args.catalog is None)
    if not fixes:
        print("known-fixes: matches=0 replies=0 errors=0")
        return 0
    store = Store(coms_dir, holder)
    check(store, strict=True, require_bound=True)
    proof = proof_path(config, coms_dir)
    if not args.reply:
        return send_suggestions(store, fixes, targets, False, proof)

    lock = FileMutex(os.path.join(coms_dir, "locks", f"known-fixes-{holder}.lock"))
    lock.acquire()
    try:
        return send_suggestions(store, fixes, targets, True, proof)
    finally:
        lock.release()


def add_parser(sub):
    parser = sub.add_parser("known-fixes", help="match ASK and BLOCKER cards against a local catalog")
    parser.add_argument("--catalog", type=Path, help="catalog path, defaults to known-fixes.txt in the state folder")
    parser.add_argument("--reply", action="store_true", help="send local RULING cards, otherwise preview counts")
    parser.add_argument("--reply-to", action="append", default=[], metavar="HOLDER",
                        help="allow replies to this local holder, repeatable")
