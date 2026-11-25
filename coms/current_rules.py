"""Render a local page of current and superseded COMS decisions."""

import os
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from tempfile import NamedTemporaryFile

from coms.decisions import DecisionLog, select_current, topic_key


def read_pins(path, optional=False):
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        if optional:
            return {}
        raise
    keys = (topic_key(line) for line in lines if line.strip() and not line.lstrip().startswith("#"))
    return dict.fromkeys(keys)


def decision_row(entry):
    topic = escape(" ".join(entry["topic"].split()))
    answer = escape(" ".join(entry["answer"].split()))
    stamp = datetime.fromtimestamp(entry["ts"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return f"<li><strong>{topic}</strong> <small>{stamp}</small><p>{answer}</p></li>"


def section(title, entries):
    rows = "\n".join(decision_row(entry) for entry in entries)
    content = f"<ul>\n{rows}\n</ul>" if rows else "<p>None</p>"
    return f"<section><h2>{title}</h2>\n{content}\n</section>"


def render(entries, pins):
    active = [entry for entry, _ in select_current(entries)]
    active_by_topic = {topic_key(row["topic"]): row for row in active}
    pinned = [active_by_topic[key] for key in pins if key in active_by_topic]
    unpinned = [row for row in active if topic_key(row["topic"]) not in pins]
    active_ids = {id(row) for row in active}
    retired = [row for row in reversed(entries) if id(row) not in active_ids]
    sections = [section("Pinned", pinned), section("Current", unpinned), section("Superseded or expired", retired)]
    return ("<!doctype html>\n<html lang=\"en\"><meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            "<title>Current COMS rules</title>\n"
            "<style>body{font:1.1rem/1.5 system-ui,sans-serif;max-width:66ch;margin:2rem auto;padding:0 1rem}"
            "li{margin-bottom:1rem} li p{margin:.15rem 0} small{margin-inline-start:.4rem}</style>\n"
            "<main><h1>Current COMS rules</h1>\n" + "\n".join(sections) + "\n</main></html>\n")


def run(args, coms_dir):
    state = Path(coms_dir)
    entries = DecisionLog(coms_dir).entries()
    pins = read_pins(args.pins or state / "rule-pins.txt", optional=args.pins is None)
    output = args.output or state / "current-rules.html"
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent, delete=False) as target:
        temporary = Path(target.name)
        try:
            target.write(render(entries, pins))
            target.close()
            os.replace(temporary, output)
        finally:
            temporary.unlink(missing_ok=True)
    print("current-rules: wrote the local decision page")
    return 0


def add_parser(sub):
    parser = sub.add_parser("current-rules", help="build a private HTML page of recorded decisions")
    parser.add_argument("--pins", type=Path, help="topic list, defaults to rule-pins.txt in the state folder")
    parser.add_argument("--output", type=Path, help="HTML path, defaults to current-rules.html in the state folder")
