"""A handled receipt can never lock its holder out: what checkpoint-ack writes, checkpoint-ack reads back.

Scenario: a holder's handled receipt grows past 1 MiB (open-loops re-serialized with indent=2), the
next acknowledgement reads it back through the 1 MiB handover-input cap, and without the fix every
later checkpoint-ack would fail with "handover input exceeds 1 MiB".
"""
import builtins
import errno
import hashlib
import io
import json
import math
import os
import random
import pathlib
import re
import stat
import tempfile
import tracemalloc
import unittest
from unittest.mock import patch

from coms import checkpoints, cli, handover, inbox
from coms.handover import MAX_PACKET
from coms.inbox import atomic_json
from coms.mutex import FileMutex, LockTimeout
from coms.store import Store
from tests.test_switchover import Sandbox

COMPACT = (",", ":")
LARGE_COMPACT_BYTES = 917000  # a synthetic open-loops object, compact: under 1 MiB
LARGE_RECEIPT_BYTES = 1049000  # the same receipt pretty-printed: over 1 MiB
E_ACUTE = chr(0xE9)  # 2 bytes as UTF-8, 6 bytes once escaped to ASCII JSON


def deep_text(depth):
    """JSON for nested empty lists, built by hand: the encoders cannot write it, the decoder still reads it."""
    return "[" * depth + "]" * depth


class ReadSpy:
    """Wraps a binary file and records each read request as (size asked, bytes returned)."""
    def __init__(self, handle, log):
        self.handle, self.log = handle, log

    def read(self, size=-1):
        data = self.handle.read(size)
        self.log.append((size, len(data)))
        return data

    def __enter__(self):
        self.handle.__enter__()
        return self

    def __exit__(self, *exc):
        return self.handle.__exit__(*exc)

    def __getattr__(self, name):
        return getattr(self.handle, name)


def spy_on_reads(target):
    """(log, patch): inside the patch, every read of target is recorded in log."""
    log, real = [], pathlib.Path.open

    def opened(self, *args, **kwargs):
        handle = real(self, *args, **kwargs)
        return ReadSpy(handle, log) if str(self) == str(target) else handle
    return log, patch.object(pathlib.Path, "open", opened)


def loops_with(open_work):
    return {"open_work": open_work, "owner_questions": [], "routines": [],
            "pending_handoffs": [], "evidence": []}


def many_small_items(target):
    """Open loops of many small dicts, about target bytes compact."""
    def item(number):
        return {"id": "AD%06d" % number, "owner": "example-holder",
                "state": "example_task_reported_and_merged",
                "original_state": "notes/example-state.json#pending.AD%06d" % number}
    def size(items):
        return len(json.dumps(loops_with(items), separators=COMPACT))
    per_item = size([item(0), item(1)]) - size([item(0)])
    count = max(1, (target - size([item(0)])) // per_item + 1)
    return loops_with([item(number) for number in range(count)])


def nested_loops(depth, chains):
    """Open loops that pretty-print to tens of megabytes: a nested list costs about 2 * depth ** 2 bytes at indent=2."""
    chain = []
    for _ in range(depth):
        chain = [chain]
    return loops_with([chain] * chains)


def loops_exactly(size, filler="x"):
    """Open loops of one padded string: size bytes as a compact UTF-8 file, or the closest fit under it."""
    base = len(json.dumps(loops_with([""]), ensure_ascii=False, separators=COMPACT).encode())
    return loops_with([filler * ((size - base) // len(filler.encode()))])


class HandledReceiptSizeTest(Sandbox):
    def setUp(self):
        super().setUp()
        self.handled = pathlib.Path(self.state) / "handled/leaving.json"
        self.delivery = pathlib.Path(self.state) / "checkpoints/leaving.json"

    def write_loops(self, body, name="open-loops.json"):
        path = self.home / name
        path.write_text(json.dumps(body, ensure_ascii=False, separators=COMPACT), encoding="utf-8")
        self.assertLessEqual(path.stat().st_size, MAX_PACKET)  # an input the tool accepts
        return path

    def acknowledge(self, loops=None):
        code, out, err = self.run_cli("inbox", "--peek", "--receipt")
        self.assertEqual(code, 0, err)
        receipt = self.home / "receipt.json"
        receipt.write_text(out)
        return self.run_cli("checkpoint-ack", str(receipt), "--pending", str(loops or self.loops))

    def acknowledge_rounds(self, loops, rounds=3):
        for number in range(1, rounds + 1):
            self.send("message %d" % number)
            code, out, err = self.acknowledge(loops)
            self.assertEqual((code, err), (0, ""), "round %d" % number)
            self.assertIn("handled checkpoint:", out)

    def legacy_receipt(self, pending):
        """Rewrite the handled receipt the way builds before this fix wrote it: indent=2."""
        record = json.loads(self.handled.read_text())
        record["pending"] = pending
        self.handled.write_text(json.dumps(record, indent=2))
        self.assertGreater(self.handled.stat().st_size, MAX_PACKET)  # the size that used to lock a holder out

    def legacy_receipt_exactly(self, size):
        """The handled receipt as an older build wrote it (indent=2), padded to exactly size bytes."""
        record = json.loads(self.handled.read_text())
        record["pending"] = many_small_items(600000)
        record["pending"]["evidence"] = [""]
        base = len(json.dumps(record, indent=2))
        record["pending"]["evidence"] = ["x" * (size - base)]
        self.handled.write_text(json.dumps(record, indent=2))
        self.assertEqual(self.handled.stat().st_size, size)

    def restore(self, note):
        """The new window's side: the holding window relays to a NEW holder, which accepts; then the old route retires."""
        self.enroll("successor")
        self.as_holder("holding")
        self.assertEqual(self.run_cli("handover", "relay", "--replay", str(note), "--to", "successor")[0], 0)
        self.as_holder("successor")
        self.assertEqual(self.run_cli("handover", "accept", str(note), "--confirm-ready")[0], 0)
        self.as_holder("holding")
        self.assertEqual(self.run_cli("handover", "retire", str(note), "--routes-confirmed")[0], 0)
        return [m["text"] for m in Store(self.state, "successor").read_inbox()]

    def assert_restored(self, note, texts):
        self.assertTrue(any('"text": "second"' in text for text in texts), "unread original relayed")
        self.assertTrue(any("HANDOVER OPEN LOOPS" in text and "finish approved lane" in text for text in texts))
        self.assertEqual(json.loads(note.read_text())["phase"], "retired")
        self.assertLessEqual(note.stat().st_size, MAX_PACKET)

    def assert_points_at_receipt(self, packet, receipt=None, checkpoint=None):
        """The packet points, by size and hash, at a private copy of exactly the bytes the receipt held."""
        entry = packet["handled"]
        receipt = self.handled.read_bytes() if receipt is None else receipt
        copy = pathlib.Path(entry["source"])
        self.assertEqual(copy.parent, self.notes)  # kept with the note, where an acknowledgement cannot reach it
        self.assertEqual(entry["origin"], str(self.handled))
        self.assertEqual(copy.read_bytes(), receipt)
        self.assertEqual((entry["bytes"], entry["sha256"]), (len(receipt), hashlib.sha256(receipt).hexdigest()))
        self.assertEqual(entry["checkpoint"], json.loads(receipt)["checkpoint"] if checkpoint is None else checkpoint)
        self.assertNotIn("pending", entry)  # the open loops stay in the copy, not in the packet
        self.assertEqual(stat.S_IMODE(copy.stat().st_mode), 0o600)

    def misordered_receipt(self, pending_bytes=100000):
        """A receipt another tool wrote: the open loops come before the checkpoint."""
        checkpoint = json.loads(self.handled.read_text())["checkpoint"]
        self.handled.write_text(json.dumps({"pending": loops_exactly(pending_bytes), "checkpoint": checkpoint}))
        self.assertGreater(self.handled.stat().st_size, inbox.HANDLED_HEAD)
        return checkpoint

    def try_note(self, loops, note_id="transition-test"):
        """Run handover note and return (code, err); the two windows are enrolled first."""
        self.enroll("holding", "orchestrator")
        self.enroll("leaving", "codex-lead")
        code, out, err = self.run_cli("handover", "note", "--to", "holding", "--role", "codex-lead",
                                      "--open-loops", str(loops), "--id", note_id)
        return code, err

    def big_misordered_receipt(self, size):
        """A receipt another tool wrote, size bytes long: the open loops, one long string, come before the checkpoint."""
        checkpoint = json.loads(self.handled.read_text())["checkpoint"]
        opening = b'{"pending": {"open_work": ["'
        closing = (b'"], "owner_questions": [], "routines": [], "pending_handoffs": [], "evidence": []}, "checkpoint": '
                   + json.dumps(checkpoint).encode() + b"}")
        with self.handled.open("wb") as stream:
            stream.write(opening + b"x" * (size - len(opening) - len(closing)) + closing)
        self.assertEqual(self.handled.stat().st_size, size)
        return checkpoint

    def kept_files(self):
        """What the handover directory holds, apart from lock files; empty when it was never made."""
        if not self.notes.exists():
            return []
        return sorted(p.name for p in self.notes.iterdir() if not p.name.endswith(".lock"))

    def test_handover_with_a_large_pretty_receipt_is_written_and_restored(self):
        self.send("first")
        self.assertEqual(self.acknowledge()[0], 0)
        self.legacy_receipt_exactly(LARGE_RECEIPT_BYTES)
        self.send("second")  # arrives after the last handled point, so the note must carry it
        note = self.note()
        packet = json.loads(note.read_text())
        self.assertLessEqual(note.stat().st_size, MAX_PACKET)
        self.assert_points_at_receipt(packet)
        self.assertEqual([m["text"] for m in packet["unread"]], ["second"])
        self.assert_restored(note, self.restore(note))
        self.assert_points_at_receipt(json.loads(note.read_text()))  # still true after retirement

    def test_handover_with_a_large_compact_receipt_is_written_and_restored(self):
        self.send("first")
        self.assertEqual(self.acknowledge(self.write_loops(many_small_items(LARGE_COMPACT_BYTES)))[0], 0)
        self.assertGreater(self.handled.stat().st_size, LARGE_COMPACT_BYTES)  # as the fixed writer saves it
        self.send("second")
        note = self.note()
        self.assert_points_at_receipt(json.loads(note.read_text()))
        self.assert_restored(note, self.restore(note))

    def test_small_receipt_is_still_copied_into_the_packet(self):
        self.send("first")
        self.assertEqual(self.acknowledge()[0], 0)
        saved = json.loads(self.handled.read_text())
        packet = json.loads(self.note().read_text())
        self.assertEqual(packet["handled"], saved)
        self.assertEqual(packet["handled"]["pending"]["owner_questions"], ["pending scope ruling"])

    def test_ack_survives_a_legacy_receipt_beyond_any_read_limit(self):
        self.send("first")
        self.assertEqual(self.acknowledge()[0], 0)
        self.legacy_receipt(nested_loops(500, 70))
        self.assertGreater(self.handled.stat().st_size, 33 * 1024 * 1024)  # past a 32 MiB reader limit
        self.send("second")
        code, out, err = self.acknowledge()
        self.assertEqual((code, err), (0, ""))
        self.assertLess(self.handled.stat().st_size, MAX_PACKET)  # replaced by the small compact receipt
        self.send("third")
        self.assertEqual(self.acknowledge()[0], 0)

    def test_receipt_peek_and_note_survive_a_legacy_receipt_beyond_any_read_limit(self):
        self.send("first")
        self.assertEqual(self.acknowledge()[0], 0)
        self.legacy_receipt(nested_loops(500, 70))
        self.assertGreater(self.handled.stat().st_size, 33 * 1024 * 1024)
        self.send("second")
        self.assertEqual(self.run_cli("inbox", "--peek", "--receipt")[0], 0)
        note = self.note()
        packet = json.loads(note.read_text())
        self.assertLessEqual(note.stat().st_size, MAX_PACKET)
        self.assert_points_at_receipt(packet)

    def test_a_receipt_with_its_checkpoint_after_the_open_loops_is_read_acknowledged_and_rewritten(self):
        self.send("first")
        self.assertEqual(self.acknowledge()[0], 0)
        checkpoint = self.misordered_receipt()
        self.send("second")
        code, out, err = self.run_cli("inbox", "--peek", "--receipt")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out)["checkpoint"]["start_offset"], checkpoint["offset"])
        code, out, err = self.acknowledge()
        self.assertEqual((code, err), (0, ""))
        self.assertTrue(self.handled.read_text().startswith('{"checkpoint":'))  # rewritten in the order readers expect
        self.send("third")
        self.assertEqual(self.acknowledge()[0], 0)

    def test_a_receipt_with_its_checkpoint_after_the_open_loops_hands_over_by_pointer(self):
        self.send("first")
        self.assertEqual(self.acknowledge()[0], 0)
        checkpoint = self.misordered_receipt()
        receipt = self.handled.read_bytes()
        self.send("second")
        note = self.note()
        packet = json.loads(note.read_text())
        self.assert_points_at_receipt(packet, receipt)
        self.assertEqual(packet["handled"]["checkpoint"], checkpoint)
        self.assert_restored(note, self.restore(note))

    def test_a_receipt_of_any_size_with_its_checkpoint_last_still_acknowledges(self):
        self.send("first")
        self.assertEqual(self.acknowledge()[0], 0)
        checkpoint = self.big_misordered_receipt(40 * 1024 * 1024)  # past the 32 MiB that a whole parse would allow
        self.send("second")
        code, out, err = self.run_cli("inbox", "--peek", "--receipt")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out)["checkpoint"]["start_offset"], checkpoint["offset"])
        code, out, err = self.acknowledge()
        self.assertEqual((code, err), (0, ""))
        self.assertLess(self.handled.stat().st_size, MAX_PACKET)  # replaced by the compact receipt, checkpoint first
        self.assertTrue(self.handled.read_text().startswith('{"checkpoint":'))
        self.send("third")
        self.assertEqual(self.acknowledge()[0], 0)

    def test_a_receipt_of_any_size_with_its_checkpoint_last_hands_over_by_pointer(self):
        self.send("first")
        self.assertEqual(self.acknowledge()[0], 0)
        checkpoint = self.big_misordered_receipt(40 * 1024 * 1024)
        receipt = self.handled.read_bytes()
        self.send("second")
        note = self.note()
        self.assertLessEqual(note.stat().st_size, MAX_PACKET)
        self.assert_points_at_receipt(json.loads(note.read_text()), receipt, checkpoint)
        self.assert_restored(note, self.restore(note))

    def test_a_receipt_nested_past_the_decoder_with_its_checkpoint_last_still_acknowledges(self):
        self.send("first")
        self.assertEqual(self.acknowledge()[0], 0)
        checkpoint = json.loads(self.handled.read_text())["checkpoint"]
        self.handled.write_text('{"pending": %s, "checkpoint": %s}' % (deep_text(100000), json.dumps(checkpoint)))
        self.send("second")
        self.assertEqual(self.run_cli("inbox", "--peek", "--receipt")[0], 0)
        code, out, err = self.acknowledge()
        self.assertEqual((code, err), (0, ""))
        self.assertTrue(self.handled.read_text().startswith('{"checkpoint":'))

    def test_a_receipt_nested_past_the_decoder_with_its_checkpoint_last_hands_over_by_pointer(self):
        self.send("first")
        self.assertEqual(self.acknowledge()[0], 0)
        checkpoint = json.loads(self.handled.read_text())["checkpoint"]
        self.handled.write_text('{"pending": %s, "checkpoint": %s}' % (deep_text(100000), json.dumps(checkpoint)))
        receipt = self.handled.read_bytes()
        self.send("second")
        note = self.note()
        self.assert_points_at_receipt(json.loads(note.read_text()), receipt, checkpoint)

    def test_a_receipt_with_no_checkpoint_is_refused_with_its_recovery(self):
        self.send("first")
        self.assertEqual(self.acknowledge()[0], 0)
        self.handled.write_text(json.dumps({"pending": loops_with(["lost its checkpoint"])}))
        self.send("second")
        code, out, err = self.run_cli("inbox", "--peek", "--receipt")
        self.assertEqual(code, 2)
        self.assertIn("invalid handled checkpoint", err)
        self.assertIn("move it aside and acknowledge again", err)
        self.assertNotIn("Traceback", err)
        code, err = self.try_note(self.loops)
        self.assertEqual(code, 2)
        self.assertIn("move it aside", err)
        self.assertEqual(self.kept_files(), [])  # the refused note left nothing behind
        moved = self.handled.with_name("leaving.json.unreadable")
        self.handled.rename(moved)  # the documented recovery: the file is kept, the whole inbox replays
        self.assertEqual(self.acknowledge()[0], 0)
        self.assertTrue(moved.exists())
        self.assertEqual(json.loads(self.handled.read_text())["checkpoint"]["start_offset"], 0)
        self.send("third")
        self.assertEqual(self.acknowledge()[0], 0)  # and it chains from there again

    def test_a_small_receipt_with_credential_like_prose_hands_over_by_pointer(self):
        self.send("first")
        self.assertEqual(self.acknowledge()[0], 0)
        record = json.loads(self.handled.read_text())
        record["pending"] = loops_with(["rotate password=example soon"])  # the open-loops check refuses this at write time
        self.handled.write_text(json.dumps(record, separators=COMPACT))
        receipt = self.handled.read_bytes()
        self.assertLess(len(receipt), handover.HANDLED_EMBED_MAX)
        self.send("second")
        note = self.note()
        self.assert_points_at_receipt(json.loads(note.read_text()), receipt)
        self.assertNotIn("password=example", note.read_text())  # the note points at the prose and never carries it

    def test_a_small_receipt_cannot_push_the_packet_past_its_cap(self):
        self.send("first")
        self.assertEqual(self.acknowledge()[0], 0)
        record = json.loads(self.handled.read_text())
        record["pending"] = loops_with(["x" * 60000])
        self.handled.write_text(json.dumps(record, separators=COMPACT))
        receipt = self.handled.read_bytes()
        form = len(json.dumps({"handled": record}, indent=2))
        self.assertLessEqual(form, handover.HANDLED_EMBED_MAX)  # small enough to be copied on its own account
        scope = self.write_loops(loops_exactly(MAX_PACKET - 40000))  # an accepted scope that leaves little room
        self.send("second")
        self.assertEqual(self.try_note(scope), (0, ""))
        note = self.notes / "transition-test.json"
        self.assertLessEqual(note.stat().st_size, MAX_PACKET)
        self.assert_points_at_receipt(json.loads(note.read_text()), receipt)
        self.assertGreater(note.stat().st_size + form, MAX_PACKET)  # copied whole, the receipt alone would break the cap

    def test_a_receipt_copy_never_overwrites_another_note(self):
        self.send("first")
        self.assertEqual(self.acknowledge(self.write_loops(many_small_items(LARGE_COMPACT_BYTES)))[0], 0)  # large: a note keeps a copy
        self.assertEqual(self.try_note(self.loops, "t.handled"), (0, ""))
        first = self.notes / "t.handled.json"
        self.as_holder("leaving")
        self.assertEqual(self.run_cli("handover", "cancel", str(first))[0], 0)  # frees the holder for a second note
        before = first.read_bytes()
        self.assertEqual(self.try_note(self.loops, "t"), (0, ""))  # its copy must not take the other note's name
        self.assertEqual(first.read_bytes(), before)
        self.assertEqual(json.loads(first.read_text())["id"], "t.handled")
        self.assertEqual(self.kept_files(), ["t.handled", "t.handled.handled", "t.handled.json", "t.json"])

    def test_an_oversized_clock_in_the_receipt_is_refused_with_a_message(self):
        self.send("first")
        self.assertEqual(self.acknowledge()[0], 0)
        record = json.loads(self.handled.read_text())
        record["checkpoint"]["last_read_at"] = 10 ** 400  # valid JSON, past what a float can hold
        self.handled.write_text(json.dumps(record, separators=COMPACT))
        code, out, err = self.run_cli("inbox", "--peek", "--receipt")
        self.assertEqual(code, 2)
        self.assertIn("coms: ", err)
        self.assertNotIn("Traceback", err)

    def test_a_small_receipt_that_pretty_prints_past_the_packet_cap_is_pointed_at(self):
        self.send("first")
        self.assertEqual(self.acknowledge(self.write_loops(nested_loops(140, 60)))[0], 0)
        receipt = self.handled.read_bytes()
        self.assertLess(len(receipt), handover.HANDLED_EMBED_MAX)  # small on disk
        self.assertGreater(len(json.dumps({"handled": json.loads(receipt)}, indent=2)), MAX_PACKET)  # huge in a packet
        self.send("second")
        note = self.note()
        packet = json.loads(note.read_text())
        self.assertLessEqual(note.stat().st_size, MAX_PACKET)
        self.assert_points_at_receipt(packet, receipt)
        self.assert_restored(note, self.restore(note))

    def test_the_deepest_receipt_whose_packet_form_fits_is_still_copied_whole(self):
        self.send("first")
        self.assertEqual(self.acknowledge()[0], 0)
        record = json.loads(self.handled.read_text())
        depth = 100
        while True:  # the deepest single chain whose form inside a packet stays within the copy limit
            record["pending"] = nested_loops(depth + 1, 1)
            if len(json.dumps({"handled": record}, indent=2)) > handover.HANDLED_EMBED_MAX:
                break
            depth += 1
        record["pending"] = nested_loops(depth, 1)
        self.assertGreater(depth, 150)
        self.handled.write_text(json.dumps(record, separators=COMPACT))
        self.send("second")
        note = self.note()  # still passes the packet's nesting check
        self.assertEqual(json.loads(note.read_text())["handled"], record)
        self.assertEqual(self.kept_files(), ["transition-test.json"])  # copied whole: no private copy

    def test_a_pointer_still_describes_the_receipt_after_the_receipt_file_is_replaced(self):
        self.send("first")
        self.assertEqual(self.acknowledge(self.write_loops(many_small_items(LARGE_COMPACT_BYTES)))[0], 0)
        receipt = self.handled.read_bytes()
        self.send("second")
        note = self.note()
        entry = json.loads(note.read_text())["handled"]
        # The holder goes on: the next acknowledgement replaces the receipt file under the same name.
        atomic_json(str(self.handled), {"checkpoint": entry["checkpoint"], "pending": loops_with(["newer"])},
                    compact=True)
        self.assertNotEqual(self.handled.read_bytes(), receipt)
        self.assert_points_at_receipt(json.loads(note.read_text()), receipt)
        self.assertEqual(self.kept_files(), ["transition-test.handled", "transition-test.json"])

    def test_the_private_copy_is_removed_when_the_note_is_refused(self):
        self.send("first")
        self.assertEqual(self.acknowledge(self.write_loops(many_small_items(LARGE_COMPACT_BYTES)))[0], 0)
        self.assertGreater(self.handled.stat().st_size, handover.HANDLED_EMBED_MAX)  # so the note makes a copy
        too_big = self.write_loops(many_small_items(LARGE_COMPACT_BYTES), "too-big.json")  # the same scope, refused whole
        code, err = self.try_note(too_big)
        self.assertEqual(code, 2)
        self.assertIn("handover packet exceeds 1 MiB", err)
        self.assertEqual(self.kept_files(), [])  # no note, no private copy, no half-written temporary
        self.assertEqual(self.try_note(self.loops)[0], 0)  # retried with a scope that fits, under the same id
        self.assertEqual(self.kept_files(), ["transition-test.handled", "transition-test.json"])

    def test_the_private_copy_stays_when_the_note_was_already_written(self):
        self.send("first")
        self.assertEqual(self.acknowledge(self.write_loops(many_small_items(LARGE_COMPACT_BYTES)))[0], 0)
        real = handover.save_packet

        def written_then_failed(path, packet):
            real(path, packet)
            raise OSError("disk went away after the note was saved")
        with patch.object(handover, "save_packet", written_then_failed):
            code, err = self.try_note(self.loops)
        self.assertEqual(code, 2)
        self.assertIn("disk went away", err)
        note = self.notes / "transition-test.json"
        self.assertTrue(note.exists())
        self.assert_points_at_receipt(json.loads(note.read_text()))  # the note is there, so its copy must be

    def test_the_receipt_is_copied_while_the_inbox_lock_is_held(self):
        self.send("first")
        self.assertEqual(self.acknowledge(self.write_loops(many_small_items(LARGE_COMPACT_BYTES)))[0], 0)
        lock = inbox.Inbox(Store(self.state, "leaving")).mutex.path  # an acknowledgement takes it to replace the receipt
        real, held = handover.HandledReceipt, []

        def watched(*args, **kwargs):
            rival = FileMutex(lock)
            try:
                rival.acquire(timeout=0)
            except LockTimeout:
                held.append(True)
            else:
                rival.release()
                held.append(False)
            return real(*args, **kwargs)
        with patch.object(handover, "HandledReceipt", watched):
            self.note()
        self.assertEqual(held, [True])  # so the packet describes the receipt that fixed its unhandled start

    def test_the_private_copy_is_removed_when_the_note_fails_after_the_copy_was_made(self):
        self.send("first")
        self.assertEqual(self.acknowledge(self.write_loops(many_small_items(LARGE_COMPACT_BYTES)))[0], 0)
        self.send("second")  # an unread original, so the note goes on to summarize it after the copy
        with patch.object(handover, "message_payload", side_effect=ValueError("summary went wrong")):
            code, err = self.try_note(self.loops)
        self.assertEqual(code, 2)
        self.assertIn("summary went wrong", err)
        self.assertEqual(self.kept_files(), [])

    def test_a_note_never_replaces_an_existing_receipt_copy(self):
        self.send("first")
        self.assertEqual(self.acknowledge(self.write_loops(many_small_items(LARGE_COMPACT_BYTES)))[0], 0)  # large: a note keeps a copy
        self.notes.mkdir(mode=0o700)
        taken = self.notes / "transition-test.handled"
        taken.write_text("an archived note's receipt")
        code, err = self.try_note(self.loops)
        self.assertEqual(code, 2)
        self.assertIn("already exists", err)
        self.assertEqual(taken.read_text(), "an archived note's receipt")
        self.assertEqual(self.kept_files(), ["transition-test.handled"])  # no note, no temporary

    def test_a_checkpoint_with_credential_like_extras_hands_over_with_redacted_prose(self):
        self.send("first")
        self.assertEqual(self.acknowledge()[0], 0)
        record = json.loads(self.handled.read_text())
        record["checkpoint"]["notes"] = "authorization: granted"  # ordinary prose that the credential check reads as a secret
        self.handled.write_text(json.dumps(record, separators=COMPACT))
        receipt = self.handled.read_bytes()
        self.send("second")
        note = self.note()
        entry = json.loads(note.read_text())["handled"]
        self.assertIn("REDACTED", entry["checkpoint"]["notes"])
        self.assertEqual(entry["checkpoint"]["offset"], record["checkpoint"]["offset"])
        self.assertNotIn("authorization: granted", note.read_text())
        self.assertEqual(pathlib.Path(entry["source"]).read_bytes(), receipt)  # the extras are kept in the private copy

    def assert_reduced_checkpoint(self, checkpoint, original):
        keys = ["version", "holder", "source", "offset", "lines", "start_offset", "start_lines",
                "prefix_hash", "start_prefix_hash", "last_read_at", "last_batch_at"]
        self.assertEqual(set(checkpoint), set(keys))
        self.assertEqual(checkpoint, {key: original[key] for key in keys})
        inbox.validate_pointer(checkpoint, "leaving", Store(self.state, "leaving")._inbox_path("leaving"))

    def test_a_delivery_checkpoint_with_credential_like_extras_hands_over_with_its_pointer_fields_only(self):
        self.send("first")
        self.assertEqual(self.acknowledge()[0], 0)
        delivery = json.loads(self.delivery.read_text())
        delivery["notes"] = "authorization: granted"
        self.delivery.write_text(json.dumps(delivery))
        self.send("second")
        note = self.note()
        packet = json.loads(note.read_text())
        self.assert_reduced_checkpoint(packet["delivery_checkpoint"], delivery)
        self.assertNotIn("authorization: granted", note.read_text())
        self.assertEqual(json.loads(self.delivery.read_text()), delivery)  # the original keeps its extras

    def test_both_checkpoints_with_credential_like_extras_hand_over_with_their_pointer_fields_only(self):
        self.send("first")
        self.assertEqual(self.acknowledge()[0], 0)
        record, delivery = json.loads(self.handled.read_text()), json.loads(self.delivery.read_text())
        record["checkpoint"]["notes"] = delivery["notes"] = "authorization: granted"
        self.handled.write_text(json.dumps(record, separators=COMPACT))
        self.delivery.write_text(json.dumps(delivery))
        receipt = self.handled.read_bytes()
        self.send("second")
        note = self.note()
        packet = json.loads(note.read_text())
        self.assert_reduced_checkpoint(packet["handled"]["checkpoint"], record["checkpoint"])
        self.assert_reduced_checkpoint(packet["delivery_checkpoint"], delivery)
        self.assertNotIn("authorization: granted", note.read_text())
        self.assertEqual(pathlib.Path(packet["handled"]["source"]).read_bytes(), receipt)

    def test_checkpoints_keep_their_extras_when_the_packet_already_passes(self):
        self.send("first")
        self.assertEqual(self.acknowledge()[0], 0)
        record, delivery = json.loads(self.handled.read_text()), json.loads(self.delivery.read_text())
        record["checkpoint"]["notes"] = delivery["notes"] = "ordinary prose"
        self.handled.write_text(json.dumps(record, separators=COMPACT))
        self.delivery.write_text(json.dumps(delivery))
        packet = json.loads(self.note().read_text())
        self.assertEqual(packet["handled"], record)
        self.assertEqual(packet["delivery_checkpoint"], delivery)

    def test_a_handover_folder_with_credential_like_words_does_not_refuse_a_note(self):
        self.notes = self.home / "password=example"
        self.notes.mkdir(mode=0o700)
        os.environ["COMS_HANDOVER_DIR"] = str(self.notes)
        self.send("first")
        self.assertEqual(self.acknowledge(self.write_loops(many_small_items(LARGE_COMPACT_BYTES)))[0], 0)
        packet = json.loads(self.note().read_text())
        self.assert_points_at_receipt(packet)
        self.assertIn("password=example", packet["handled"]["source"])

    def receipt_field_with_credential_words(self, field):
        """A receipt another tool wrote, with prose where a path might be: it is pointed at, never copied into the note."""
        self.send("first")
        self.assertEqual(self.acknowledge()[0], 0)
        record = json.loads(self.handled.read_text())
        record[field] = "password=actual-credential"
        self.handled.write_text(json.dumps(record))
        note = self.note()
        self.assert_points_at_receipt(json.loads(note.read_text()))  # the words stay in the private copy
        self.assertNotIn("actual-credential", note.read_text())

    def test_credential_words_in_a_receipts_own_source_stay_out_of_the_note(self):
        self.receipt_field_with_credential_words("source")

    def test_credential_words_in_a_receipts_own_origin_stay_out_of_the_note(self):
        self.receipt_field_with_credential_words("origin")

    def test_credential_like_open_loop_prose_is_redacted_in_a_handover_note(self):
        self.notes = self.home / "password=example"
        self.notes.mkdir(mode=0o700)
        os.environ["COMS_HANDOVER_DIR"] = str(self.notes)
        loops = self.write_loops(loops_with(["password=actual-credential"]))
        code, err = self.try_note(loops)
        self.assertEqual(code, 0, err)
        note = self.notes / "transition-test.json"
        self.assertNotIn("actual-credential", note.read_text())
        self.assertIn("REDACTED", note.read_text())
        self.assertIn("actual-credential", loops.read_text())

    def test_an_oversized_number_or_a_deep_value_anywhere_becomes_a_coms_message(self):
        for error in (OverflowError("int too large to convert to float"), RecursionError("maximum recursion depth exceeded")):
            def explode(args, cdir, error=error):
                raise error
            with patch.dict(cli.DISPATCH, {"status": explode}):
                code, out, err = self.run_cli("status")
            self.assertEqual(code, 2)
            self.assertTrue(err.startswith("coms: "), err)

    def test_a_malformed_delivery_checkpoint_refuses_an_acknowledgement_before_anything_changes(self):
        self.send("first")
        self.assertEqual(self.acknowledge()[0], 0)
        self.send("second")
        code, out, err = self.run_cli("inbox", "--peek", "--receipt")
        self.assertEqual(code, 0, err)
        receipt = self.home / "receipt.json"
        receipt.write_text(out)
        before = self.handled.read_bytes()
        for text, message in (('{"offset": null}', "invalid inbox checkpoint"), ('{"offset": "9"}', "invalid inbox checkpoint"),
                              ('{"offset": true}', "invalid inbox checkpoint"), ('[]', "must be an object")):
            self.delivery.write_text(text)
            code, out, err = self.run_cli("checkpoint-ack", str(receipt), "--pending", str(self.loops))
            self.assertEqual(code, 2, text)
            self.assertTrue(err.startswith("coms: ") and message in err, err)
            self.assertEqual((self.handled.read_bytes(), self.delivery.read_text()), (before, text))
        self.delivery.unlink()  # with no delivery checkpoint at all, the same receipt is acknowledged
        self.assertEqual(self.run_cli("checkpoint-ack", str(receipt), "--pending", str(self.loops))[0], 0)
        self.assertNotEqual(self.handled.read_bytes(), before)

    def test_an_oversized_clock_in_an_inbox_message_is_skipped_with_a_warning(self):
        path = pathlib.Path(Store(self.state, "leaving")._inbox_path("leaving"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"from": "sender", "ts": 10 ** 400, "text": "hello"}) + "\n")
        code, out, err = self.run_cli("inbox", "--peek")
        self.assertEqual(code, 0)
        self.assertIn("malformed inbox line", err)
        self.assertNotIn("Traceback", err)

    def test_a_small_receipt_leaves_no_private_copy_behind(self):
        self.send("first")
        self.assertEqual(self.acknowledge()[0], 0)
        self.note()
        self.assertEqual(self.kept_files(), ["transition-test.json"])

    def test_deeply_nested_pending_is_refused_with_a_message_by_acknowledge(self):
        deep = self.home / "deep-loops.json"
        deep.write_text('{"open_work": %s, "owner_questions": [], "routines": [], "pending_handoffs": [], "evidence": []}'
                        % deep_text(3000))  # 6 KB: the decoder reads it, a Python walk of it cannot
        self.send("first")
        code, out, err = self.acknowledge(deep)
        self.assertEqual(code, 2)
        self.assertIn("coms: ", err)
        self.assertIn("nesting exceeds safe JSON limits", err)
        self.assertNotIn("Traceback", err)
        self.assertFalse(self.handled.exists())
        self.assertEqual(self.acknowledge()[0], 0)  # refused once, not locked out

    def test_deeply_nested_open_loops_are_refused_with_a_message_by_the_handover_note(self):
        deep = self.home / "deep-loops.json"
        deep.write_text('{"open_work": %s, "owner_questions": [], "routines": [], "pending_handoffs": [], "evidence": []}'
                        % deep_text(3000))
        code, err = self.try_note(deep)
        self.assertEqual(code, 2)
        self.assertIn("nesting exceeds safe JSON limits", err)
        self.assertNotIn("Traceback", err)
        self.assertEqual(self.kept_files(), [])

    def test_a_small_receipt_with_deep_pending_hands_over_without_a_traceback(self):
        self.send("first")
        self.assertEqual(self.acknowledge()[0], 0)
        checkpoint = json.loads(self.handled.read_text())["checkpoint"]
        self.handled.write_text('{"checkpoint": %s, "pending": %s}' % (json.dumps(checkpoint), deep_text(3000)))
        self.assertLess(self.handled.stat().st_size, 8000)  # small on disk, 3,000 levels deep
        receipt = self.handled.read_bytes()
        self.send("second")
        self.assertEqual(self.run_cli("inbox", "--peek", "--receipt")[0], 0)
        note = self.note()
        self.assert_points_at_receipt(json.loads(note.read_text()), receipt, checkpoint)  # json.loads cannot read it on 3.9

    def test_large_open_loops_acknowledge_every_time(self):
        loops = self.write_loops(many_small_items(LARGE_COMPACT_BYTES))
        self.acknowledge_rounds(loops)
        self.assertEqual(json.loads(self.handled.read_text())["pending"], many_small_items(LARGE_COMPACT_BYTES))

    def test_open_loops_at_the_input_cap_acknowledge_every_time(self):
        loops = self.write_loops(loops_exactly(MAX_PACKET))
        self.assertEqual(loops.stat().st_size, MAX_PACKET)
        self.acknowledge_rounds(loops)
        # The pointer and hashes ride along, so even a compact receipt is past the packet cap.
        self.assertGreater(self.handled.stat().st_size, MAX_PACKET)

    def test_non_ascii_open_loops_triple_in_size_and_still_read_back(self):
        loops = self.write_loops(loops_exactly(MAX_PACKET, E_ACUTE))
        self.acknowledge_rounds(loops)
        self.assertGreater(self.handled.stat().st_size, 2 * MAX_PACKET)

    def test_handled_receipt_is_compact_ascii_and_private(self):
        self.send("compact please")
        self.assertEqual(self.acknowledge()[0], 0)
        text = self.handled.read_text()
        self.assertEqual(text, json.dumps(json.loads(text), separators=COMPACT))
        self.assertNotIn("\n", text)
        self.assertEqual(stat.S_IMODE(self.handled.stat().st_mode), 0o600)

    def test_legacy_pretty_receipt_over_one_mib_is_read_and_replaced(self):
        self.send("first")
        self.assertEqual(self.acknowledge()[0], 0)
        self.legacy_receipt(many_small_items(LARGE_COMPACT_BYTES))
        self.send("second")
        code, out, err = self.acknowledge()
        self.assertEqual((code, err), (0, ""))
        saved = json.loads(self.handled.read_text())
        self.assertEqual(saved["pending"]["owner_questions"], ["pending scope ruling"])
        self.assertLess(self.handled.stat().st_size, MAX_PACKET)
        self.send("third")
        self.assertEqual(self.acknowledge()[0], 0)

    def test_receipt_over_the_write_limit_is_refused_before_anything_changes(self):
        with patch.object(handover, "HANDLED_WRITE_MAX", 2000):
            self.send("first")
            self.assertEqual(self.acknowledge()[0], 0)
            before = (self.handled.read_bytes(), self.delivery.read_bytes())
            self.send("second")
            code, out, err = self.acknowledge(self.write_loops(loops_exactly(5000)))
            self.assertEqual(code, 2)
            self.assertIn("handled/leaving.json would be", err)
            self.assertIn("nothing was written", err)
            self.assertEqual((self.handled.read_bytes(), self.delivery.read_bytes()), before)
            for folder in (self.handled.parent, self.delivery.parent):
                self.assertEqual([p.name for p in folder.iterdir() if p.name.startswith(".")], [])
            self.assertEqual(self.acknowledge()[0], 0)  # refused once, not locked out

    def test_receipt_checkpoint_cannot_carry_a_payload_into_state_files(self):
        self.send("first")
        receipt = json.loads(self.run_cli("inbox", "--peek", "--receipt")[1])
        receipt["checkpoint"]["notes"] = [1] * 400000
        path = self.home / "padded-receipt.json"
        path.write_text(json.dumps(receipt, separators=COMPACT))
        self.assertLessEqual(path.stat().st_size, MAX_PACKET)
        code, _, err = self.run_cli("checkpoint-ack", str(path), "--pending", str(self.loops))
        self.assertEqual(code, 2)
        self.assertIn("larger than an inbox pointer", err)
        self.assertFalse(self.handled.exists())
        self.assertEqual(self.acknowledge()[0], 0)

    def test_staged_delivery_receipt_cannot_carry_a_payload_into_the_checkpoint(self):
        self.send("watch original")
        staged = self.home / "watch-delivery.json"
        self.assertEqual(self.run_cli("inbox", "--peek", "--delivery-receipt", str(staged))[0], 0)
        saved = json.loads(staged.read_text())
        saved["checkpoint"]["notes"] = [1] * 400000
        staged.write_text(json.dumps(saved, separators=COMPACT))
        self.assertLessEqual(staged.stat().st_size, MAX_PACKET)
        before = self.delivery.read_bytes() if self.delivery.exists() else None
        code, _, err = self.run_cli("checkpoint-deliver", str(staged))
        self.assertEqual(code, 2)
        self.assertIn("larger than an inbox pointer", err)
        self.assertEqual(self.delivery.read_bytes() if self.delivery.exists() else None, before)

    def test_pending_input_keeps_the_one_mib_cap(self):
        self.send("first")
        oversize = self.home / "oversize.json"
        with oversize.open("wb") as stream:
            stream.truncate(MAX_PACKET + 1)  # sparse: the size check runs before any parse
        code, _, err = self.acknowledge(oversize)
        self.assertEqual(code, 2)
        self.assertIn("handover input exceeds 1 MiB", err)
        self.assertFalse(self.handled.exists())


class HandledReceiptLimitsTest(unittest.TestCase):
    def test_write_limit_covers_the_worst_case_growth_of_an_accepted_input(self):
        # loops_file caps open loops at MAX_PACKET as a file, but ASCII re-serialization grows them
        # by up to about 4x (a 2-byte character becomes a 6-byte escape, 1e15 becomes
        # 1000000000000000.0). A receipt the tool accepted must stay under the write limit.
        self.assertGreaterEqual(handover.HANDLED_WRITE_MAX, 4 * MAX_PACKET + 64 * 1024)

    def test_valid_time_refuses_what_a_float_cannot_hold_instead_of_raising(self):
        self.assertTrue(inbox.valid_time(1700000000))
        self.assertTrue(inbox.valid_time(1700000000.5))
        for bad in (10 ** 400, -10 ** 400, float("inf"), float("nan"), True, "10", None, -1, 253402300800):
            self.assertFalse(inbox.valid_time(bad), repr(bad)[:30])

    def test_safe_payload_refuses_nesting_past_its_cap_with_a_message(self):
        def nested(depth):
            value = []
            for _ in range(depth - 1):
                value = [value]
            return value
        self.assertEqual(handover.safe_payload({"a": nested(handover.MAX_NESTING - 1)}), {"a": nested(handover.MAX_NESTING - 1)})
        with self.assertRaisesRegex(ValueError, "nesting exceeds safe JSON limits"):
            handover.safe_payload({"a": nested(handover.MAX_NESTING)})  # one level too deep
        with self.assertRaisesRegex(ValueError, "nesting exceeds safe JSON limits"):
            handover.safe_payload({"a": nested(50000)})  # far past any recursion limit
        with self.assertRaisesRegex(ValueError, "possible credential field"):
            handover.safe_payload({"a": [{"password": "x"}]})  # the walk still finds credentials

    def test_the_nesting_cap_leaves_room_for_the_deepest_receipt_a_packet_copies(self):
        # A list nested d levels pretty-prints to about 2 * d ** 2 bytes, and the packet adds about four levels around it.
        self.assertGreater(handover.MAX_NESTING, math.isqrt(handover.HANDLED_EMBED_MAX // 2) + 4)

    def test_a_pointer_checkpoint_the_encoder_gives_up_on_is_refused_not_a_traceback(self):
        with patch.object(checkpoints.json, "dumps", side_effect=RecursionError("maximum recursion depth exceeded")):
            with self.assertRaisesRegex(ValueError, "larger than an inbox pointer"):
                checkpoints.require_plain_pointer({"version": 1})

    def test_a_deeply_nested_pointer_checkpoint_is_refused_on_every_python(self):
        deep = []
        for _ in range(3000):
            deep = [deep]
        with self.assertRaisesRegex(ValueError, "larger than an inbox pointer"):
            checkpoints.require_plain_pointer({"version": 1, "notes": deep})

    def test_packet_size_check_reports_nesting_when_the_encoder_gives_up(self):
        with patch.object(handover.json, "dumps", side_effect=RecursionError("maximum recursion depth exceeded")):
            with self.assertRaisesRegex(ValueError, "nesting exceeds safe JSON limits"):
                handover.packet_size_check({"open_loops": []})

    def test_a_deeply_nested_packet_is_refused_on_every_python(self):
        deep = []
        for _ in range(3000):
            deep = [deep]
        with self.assertRaises(ValueError):  # the nesting message where the encoder fails, the size message where it copes
            handover.packet_size_check({"open_loops": deep})

    def test_atomic_json_limit_refuses_before_touching_anything(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = pathlib.Path(folder).resolve()
            target = folder / "state" / "holder.json"
            atomic_json(str(target), {"keep": [1]})
            before = target.read_bytes()
            big = {"big": "x" * 100}
            size = len(json.dumps(big, separators=COMPACT))
            message = "state/holder.json would be %d bytes, over its 50-byte limit; nothing was written" % size
            with self.assertRaisesRegex(ValueError, re.escape(message)):
                atomic_json(str(target), big, compact=True, limit=50)
            self.assertEqual(target.read_bytes(), before)
            self.assertEqual(sorted(os.listdir(target.parent)), ["holder.json"])

    def test_atomic_json_default_format_is_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            target = pathlib.Path(folder).resolve() / "plain.json"
            atomic_json(str(target), {"a": [1, 2], "b": E_ACUTE})
            self.assertEqual(target.read_text(), json.dumps({"a": [1, 2], "b": E_ACUTE}, indent=2))
            atomic_json(str(target), {"a": [1, 2]}, compact=True, limit=100)
            self.assertEqual(target.read_text(), '{"a":[1,2]}')


class CheckpointFinderTest(unittest.TestCase):
    """inbox.find_checkpoint: the first top-level "checkpoint" of a JSON object, whatever surrounds it."""

    def find(self, data, chunk=None):
        if chunk is None:
            return inbox.find_checkpoint(io.BytesIO(data))
        with patch.object(inbox, "HANDLED_HEAD", chunk):
            return inbox.find_checkpoint(io.BytesIO(data))

    @staticmethod
    def random_text(rng):
        alphabet = ["a", "b", " ", '"', "\\", "{", "}", "[", "]", ",", ":", "/", "\n", "\t", E_ACUTE, chr(0x4E2D),
                    chr(0x1F600), "checkpoint"]
        return "".join(rng.choice(alphabet) for _ in range(rng.randrange(12)))

    def random_value(self, rng, depth=0):
        kind = rng.randrange(8 if depth < 4 else 5)
        if kind == 0:
            return rng.randint(-10 ** 6, 10 ** 6)
        if kind == 1:
            return rng.random() * 10 ** rng.randint(-3, 9)
        if kind == 2:
            return rng.choice([True, False, None])
        if kind in (3, 4):
            return self.random_text(rng)
        if kind in (5, 6):
            return [self.random_value(rng, depth + 1) for _ in range(rng.randrange(4))]
        return {self.random_text(rng): self.random_value(rng, depth + 1) for _ in range(rng.randrange(4))}

    def random_receipt(self, rng):
        entries = [(self.random_text(rng) or "k", self.random_value(rng)) for _ in range(rng.randrange(1, 6))]
        entries = [(key, value) for key, value in entries if key != "checkpoint"]
        entries.append(("checkpoint", {self.random_text(rng): self.random_value(rng) for _ in range(rng.randrange(4))}))
        rng.shuffle(entries)
        entries.insert(rng.randrange(len(entries) + 1), ("decoy", {"checkpoint": {"offset": 9}}))
        receipt = dict(entries)
        options = {"ensure_ascii": rng.random() < 0.5, "indent": rng.choice([None, 0, 1, 2, 4, "\t"]),
                   "separators": rng.choice([None, (",", ":"), (", ", ": "), (" ,", " : ")])}
        return receipt, json.dumps(receipt, **options).encode()

    def test_agrees_with_the_json_decoder_on_random_receipts_in_any_layout(self):
        rng = random.Random(12345)
        for case in range(300):
            receipt, data = self.random_receipt(rng)
            for chunk in (None, 1, 2, 3, 5, 64):  # chunk edges fall everywhere: inside strings, escapes, keys, values
                self.assertEqual(self.find(data, chunk), receipt["checkpoint"], (case, chunk, data[:200]))

    def test_finds_a_checkpoint_that_follows_what_the_decoder_cannot_read(self):
        checkpoint = {"offset": 7}
        text = ('{"pending": %s, "checkpoint": %s}' % (deep_text(100000), json.dumps(checkpoint))).encode()
        self.assertEqual(self.find(text), checkpoint)
        self.assertEqual(self.find(text, 7), checkpoint)

    def test_memory_does_not_depend_on_the_size_of_the_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder).resolve() / "big.json"
            with path.open("wb") as stream:
                stream.write(b'{"pending": "')
                for _ in range(40):
                    stream.write(b"x" * (1 << 20))
                stream.write(b'", "checkpoint": {"offset": 7}}')
            tracemalloc.start()
            try:
                found = inbox.handled_checkpoint(path)
                peak = tracemalloc.get_traced_memory()[1]
            finally:
                tracemalloc.stop()
            self.assertEqual(found, {"offset": 7})
            self.assertLess(peak, 2 * 1024 * 1024)  # a few 64 KiB chunks, never the 40 MiB

    def test_memory_stays_small_for_a_file_nested_hundreds_of_thousands_of_levels_deep(self):
        source = io.BytesIO(b'{"pending": ' + b"[" * 200000 + b"]" * 200000 + b', "checkpoint": {"offset": 7}}')
        tracemalloc.start()
        try:
            found = inbox.find_checkpoint(source)
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertEqual(found, {"offset": 7})
        self.assertLess(peak, 1024 * 1024)  # one byte per level of nesting, plus a chunk

    def test_a_leading_checkpoint_costs_one_read_whatever_the_size_of_the_file(self):
        with tempfile.TemporaryDirectory() as folder:
            huge = pathlib.Path(folder).resolve() / "huge.json"  # a valid head, then 40 MiB that is never read
            with huge.open("wb") as stream:
                stream.write(b'{"checkpoint": {"version": 1, "offset": 7, "lines": 2}, "pending": ')
                stream.truncate(40 * 1024 * 1024)
            log, spy = spy_on_reads(huge)
            with spy:
                self.assertEqual(inbox.handled_checkpoint(huge), {"version": 1, "offset": 7, "lines": 2})
            self.assertEqual(log, [(inbox.HANDLED_HEAD, inbox.HANDLED_HEAD)])  # the budget is one chunk, not the file

    def test_the_first_top_level_checkpoint_wins_and_look_alikes_are_ignored(self):
        text = (b'{"pending": {"checkpoint": {"offset": 9}}, "note": "checkpoint", '
                b'"check\\u0070oint": {"offset": 1}, "checkpoint": {"offset": 2}}')
        self.assertEqual(self.find(text), {"offset": 1})  # the escaped spelling is the same key, and it comes first
        self.assertEqual(self.find(text, 3), {"offset": 1})
        self.assertEqual(self.find(b' \n{"Checkpoint": 1, "checkpoints": 2, "checkpoint": {}}'), {})

    def test_refuses_what_is_not_a_receipt_and_says_how_to_recover(self):
        for text in (b"", b"  ", b"5", b"[1, 2]", b"{}", b'{"pending": 1}', b'{"checkpoint": 5}', b'{"checkpoint": [1]}',
                     b'{"checkpoint": "x"}', b'{"checkpoint": {"offset": 1', b'{"a": [1}, "checkpoint": {}}',
                     b'x{"checkpoint": {}}', b'{"x": {"checkpoint": {"offset": 1}}}', b'{"pending": "checkpoint": {}}',
                     b'{"checkpoint": {"a": [}}}', b'["checkpoint": {"offset": 1}]', b'{"checkpoint": "x" {"offset": 1}}',
                     b'{"checkpoint": ' + b"[" * 30000 + b"]" * 30000 + b"}"):
            for chunk in (None, 4):
                with self.assertRaisesRegex(ValueError, "invalid handled checkpoint.*move it aside and acknowledge again"):
                    self.find(text, chunk)

    def test_a_checkpoint_the_decoder_gives_up_on_is_refused_not_a_traceback(self):
        with patch.object(inbox.json, "loads", side_effect=RecursionError("maximum recursion depth exceeded")):
            with self.assertRaisesRegex(ValueError, "invalid handled checkpoint.*move it aside"):
                self.find(b'{"checkpoint": {"offset": 1}}')

    def test_a_checkpoint_larger_than_an_inbox_pointer_is_refused(self):
        value = json.dumps({"offset": 1, "notes": "x" * 2000}).encode()
        text = b'{"pending": [], "checkpoint": ' + value + b"}"
        for chunk in (None, 7):
            with patch.object(inbox, "HANDLED_CHECKPOINT_MAX", len(value)):
                self.assertEqual(self.find(text, chunk)["offset"], 1)  # exactly at the limit
            with patch.object(inbox, "HANDLED_CHECKPOINT_MAX", len(value) - 1):
                with self.assertRaisesRegex(ValueError, "larger than an inbox pointer"):
                    self.find(text, chunk)

    def test_a_receipt_that_is_not_a_regular_file_is_refused_not_read(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = pathlib.Path(folder).resolve()
            real = folder / "real.json"
            real.write_text('{"checkpoint": {"offset": 1}}')
            link = folder / "link.json"
            link.symlink_to(real)
            with self.assertRaisesRegex(ValueError, "not a symlink"):
                inbox.handled_checkpoint(link)
            with self.assertRaisesRegex(ValueError, "not a symlink"):
                inbox.handled_checkpoint(folder)  # a directory
            with self.assertRaises(FileNotFoundError):
                inbox.handled_checkpoint(folder / "absent.json")


class HandledReceiptTest(unittest.TestCase):
    """handover.HandledReceipt: one read of a receipt, then a copy of it in the packet or a pointer at a private copy."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = pathlib.Path(self.tmp.name).resolve()
        self.root = self.folder / "notes"
        self.root.mkdir(mode=0o700)
        self.path = self.folder / "holder.json"

    def held(self, text):
        self.path.write_text(text) if isinstance(text, str) else self.path.write_bytes(text)
        return handover.HandledReceipt(self.path, self.root)

    def kept(self):
        return sorted(os.listdir(self.root))

    def test_pointer_fields_are_the_keys_validate_pointer_reads(self):
        self.assertEqual(set(handover.POINTER_FIELDS), {"version", "holder", "source", "offset", "lines", "start_offset", "start_lines", "prefix_hash", "start_prefix_hash", "last_read_at", "last_batch_at"})

    def test_only_the_packets_own_path_fields_are_exempt_from_the_credential_scan(self):
        packet = {"store": "/password=example", "handled": {"source": "/secret=example", "origin": "/api_key=example"},
                  "lanes": [{"last_message": {"source": "/authorization=example"}}, {"last_message": None}]}
        original, pointer = json.loads(json.dumps(packet)), packet["handled"]
        self.assertTrue(handover.acceptable(packet, pointer))
        self.assertEqual(packet, original)
        self.assertFalse(handover.acceptable(packet))  # without the pointer the note built, handled is scanned whole
        for field in ("source", "origin"):  # an embedded receipt is another writer's data, its source included
            embedded = {"checkpoint": {"offset": 5}, field: "password=credential"}
            self.assertFalse(handover.acceptable(dict(packet, handled=embedded), pointer), field)
        for field in ("open_loops", "inbox_checkpoint"):
            source = dict(packet, **{field: {"source": "/password=credential"}})
            self.assertNotIn("password=credential", json.dumps(handover.safe_packet(source, pointer)))
        self.assertFalse(handover.acceptable(dict(packet, delivery_checkpoint={"source": "/password=credential"}), pointer))
        for field in ("checkpoint", "pending"):
            handled = dict(pointer, **{field: {"source": "/password=credential"}})
            sanitized = handover.safe_packet(dict(packet, handled=handled), handled)
            self.assertNotIn("password=credential", json.dumps(sanitized["handled"][field]))
        lane = {"last_message": {"source": "/password=example", "notes": "password=credential"}}
        sanitized = handover.safe_packet(dict(packet, lanes=[lane]), pointer)
        self.assertNotIn("password=credential", sanitized["lanes"][0]["last_message"]["notes"])
        self.assertEqual(sanitized["lanes"][0]["last_message"]["source"], "/password=example")
        self.assertFalse(handover.acceptable(dict(packet, store={"password": "credential"}), pointer))
        with patch.object(handover, "MAX_PACKET", 100):
            self.assertFalse(handover.acceptable(packet, pointer))  # path bytes still count against the cap

    def test_a_receipt_is_copied_whole_while_its_packet_form_fits_and_pointed_at_beyond_it(self):
        record = {"checkpoint": {"offset": 5}, "pending": {"open_work": ["a"]}, "message_hashes": ["f" * 64]}
        form = len(json.dumps({"handled": record}, indent=2))  # what the record costs inside a packet
        text = json.dumps(record, separators=COMPACT)
        with patch.object(handover, "HANDLED_EMBED_MAX", form):
            held = self.held(text)
            self.assertEqual(held.embedded(), record)  # at the limit: copied whole
            held.release(keep_copy=False)
        self.assertEqual(self.kept(), [])  # and nothing was kept
        with patch.object(handover, "HANDLED_EMBED_MAX", form - 1):
            held = self.held(text)
            self.assertIsNone(held.embedded())  # one byte over: a pointer
            account = held.pointer("t2")
        raw, copy = self.path.read_bytes(), self.root / "t2.handled"
        self.assertEqual(account, {"source": str(copy), "origin": str(self.path), "bytes": len(raw),
                                   "sha256": hashlib.sha256(raw).hexdigest(), "checkpoint": record["checkpoint"],
                                   "omitted": account["omitted"]})
        self.assertIn("private copy", account["omitted"])
        self.assertEqual(copy.read_bytes(), raw)
        self.assertEqual(stat.S_IMODE(copy.stat().st_mode), 0o600)
        held.release(keep_copy=True)
        self.assertEqual(self.kept(), ["t2.handled"])  # the temporary is gone, the copy stays

    def test_a_small_receipt_too_deep_to_copy_is_pointed_at(self):
        for depth in (3000, 30000):  # past the pretty encoder; then past the decoder as well
            text = '{"checkpoint": {"offset": 5}, "pending": %s}' % deep_text(depth)
            held = self.held(text)
            self.assertLess(len(text), handover.HANDLED_EMBED_MAX + 1)
            self.assertIsNone(held.embedded(), depth)
            self.assertEqual((held.pointer("t")["checkpoint"], (self.root / "t.handled").read_text()), ({"offset": 5}, text))
            held.release(keep_copy=False)

    def test_a_record_that_disagrees_with_the_reader_is_pointed_at(self):
        held = self.held('{"checkpoint": {"offset": 5}, "pending": {}}')
        with patch.object(handover, "handled_checkpoint", return_value={"offset": 6}):  # the two readers disagree
            self.assertIsNone(held.embedded())
        self.assertEqual(held.embedded(), {"checkpoint": {"offset": 5}, "pending": {}})  # and here they agree
        held.release(keep_copy=False)

    def test_a_private_copy_is_never_replaced(self):
        taken = self.root / "t.handled"
        taken.write_text("the receipt an older note points at")
        held = self.held('{"checkpoint": {"offset": 5}}')
        with self.assertRaisesRegex(ValueError, "already exists.*another note id"):
            held.pointer("t")
        held.release(keep_copy=False)  # it never owned that file, so it leaves it alone
        self.assertEqual(taken.read_text(), "the receipt an older note points at")
        self.assertEqual(self.kept(), ["t.handled"])
        elsewhere = self.folder / "elsewhere"
        elsewhere.write_text("kept")
        taken.unlink()
        taken.symlink_to(elsewhere)  # a symlink in the way is refused too, and what it points at is untouched
        held = self.held('{"checkpoint": {"offset": 5}}')
        with self.assertRaisesRegex(ValueError, "already exists"):
            held.pointer("t")
        held.release(keep_copy=False)
        self.assertEqual(elsewhere.read_text(), "kept")

    def test_a_private_copy_is_published_without_hard_links(self):
        held = self.held('{"checkpoint": {"offset": 5}, "pending": "' + "x" * (2 << 20) + '"}')
        raw = self.path.read_bytes()
        real_open, log = builtins.open, []

        def opened(file, *args, **kwargs):
            handle = real_open(file, *args, **kwargs)
            return ReadSpy(handle, log) if str(file) == str(held.temporary) else handle
        with patch.object(handover.os, "link", side_effect=OSError(errno.EOPNOTSUPP, "Operation not supported")), \
                patch("builtins.open", opened):
            account = held.pointer("t")
        copy = self.root / "t.handled"
        self.assertEqual(copy.read_bytes(), raw)
        self.assertEqual(account["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(stat.S_IMODE(copy.stat().st_mode), 0o600)
        self.assertEqual(held.copy, copy)
        self.assertTrue(all(asked == 1 << 20 for asked, _ in log), log)
        self.assertEqual(sum(got for _, got in log), len(raw))
        self.assertEqual(self.kept(), ["t.handled"])
        held.release(keep_copy=True)

    def test_without_hard_links_a_taken_name_or_symlink_is_left_untouched(self):
        taken, elsewhere = self.root / "t.handled", self.folder / "elsewhere"
        elsewhere.write_text("kept")
        for symlink in (False, True):
            taken.symlink_to(elsewhere) if symlink else taken.write_text("archived receipt")
            held = self.held('{"checkpoint": {"offset": 5}}')
            with patch.object(handover.os, "link", side_effect=OSError(errno.EOPNOTSUPP, "Operation not supported")):
                with self.assertRaisesRegex(ValueError, "already exists.*another note id"):
                    held.pointer("t")
            self.assertIsNone(held.copy)
            held.release(keep_copy=False)
            self.assertEqual(taken.is_symlink(), symlink)
            self.assertEqual(taken.read_text(), "kept" if symlink else "archived receipt")
            self.assertEqual(elsewhere.read_text(), "kept")
            self.assertEqual(self.kept(), ["t.handled"])
            taken.unlink()

    def test_a_failure_while_copying_without_hard_links_removes_the_destination(self):
        held = self.held('{"checkpoint": {"offset": 5}, "pending": "' + "x" * (2 << 20) + '"}')
        raw, real_open = self.path.read_bytes(), builtins.open

        def opened(file, *args, **kwargs):
            handle = real_open(file, *args, **kwargs)
            return ReadSpy(handle, []) if str(file) == str(held.temporary) else handle
        with patch.object(handover.os, "link", side_effect=OSError(errno.EOPNOTSUPP, "Operation not supported")), \
                patch("builtins.open", opened), \
                patch.object(ReadSpy, "read", side_effect=[raw[:1 << 20], OSError("copy failed")]):
            with self.assertRaisesRegex(OSError, "copy failed"):
                held.pointer("t")
        self.assertFalse((self.root / "t.handled").exists())
        self.assertIsNone(held.copy)
        held.release(keep_copy=False)
        self.assertEqual(self.kept(), [])

    def test_a_partial_copy_that_cannot_be_removed_at_once_is_removed_by_release(self):
        held = self.held('{"checkpoint": {"offset": 5}, "pending": "' + "x" * (2 << 20) + '"}')
        raw, real_open, real_unlink = self.path.read_bytes(), builtins.open, pathlib.Path.unlink
        final, refused = self.root / "t.handled", []

        def opened(file, *args, **kwargs):
            handle = real_open(file, *args, **kwargs)
            return ReadSpy(handle, []) if str(file) == str(held.temporary) else handle

        def unlink(path, *args, **kwargs):
            if path == final and not refused:  # the first removal of the partial copy fails
                refused.append(path)
                raise OSError("destination cannot be removed")
            return real_unlink(path, *args, **kwargs)
        with patch.object(handover.os, "link", side_effect=OSError(errno.EOPNOTSUPP, "Operation not supported")), \
                patch("builtins.open", opened), \
                patch.object(ReadSpy, "read", side_effect=[raw[:1 << 20], OSError("copy failed")]), \
                patch.object(pathlib.Path, "unlink", unlink):
            with self.assertRaisesRegex(OSError, "destination cannot be removed"):
                held.pointer("t")
        self.assertEqual((final.exists(), held.copy), (True, final))  # still there, and still named for release()
        held.release(keep_copy=False)
        self.assertEqual(self.kept(), [])

    def test_release_removes_the_published_copy_even_if_the_temporary_cannot_be_removed(self):
        held = self.held('{"checkpoint": {"offset": 5}}')
        held.pointer("t")
        pathlib.Path(held.temporary).write_text("temporary left behind")
        real_unlink = os.unlink

        def unlink(path, *args, **kwargs):
            if str(path) == str(held.temporary):
                raise OSError("temporary cannot be removed")
            return real_unlink(path, *args, **kwargs)
        with patch.object(handover.os, "unlink", unlink):
            with self.assertRaisesRegex(OSError, "temporary cannot be removed"):
                held.release(keep_copy=False)
        self.assertFalse((self.root / "t.handled").exists())
        held.release(keep_copy=False)
        self.assertEqual(self.kept(), [])

    def test_a_receipt_with_a_repeated_key_is_pointed_at_so_no_value_is_lost(self):
        for text in ('{"checkpoint": {"offset": 5}, "pending": {"open_work": ["first"]}, "pending": {"open_work": ["second"]}}',
                     '{"checkpoint": {"offset": 5}, "pending": {"open_work": ["a"], "open_work": ["b"]}}'):
            held = self.held(text)
            self.assertIsNone(held.embedded())  # the decoder would keep one value and lose the other
            held.pointer("t")
            self.assertEqual((self.root / "t.handled").read_text(), text)  # both survive in the private copy
            held.release(keep_copy=True)
            (self.root / "t.handled").unlink()

    def test_a_receipt_padded_past_the_copy_limit_is_pointed_at(self):
        held = self.held('{"checkpoint": {"offset": 5}}' + " " * handover.HANDLED_EMBED_MAX)  # valid, and small in substance
        self.assertIsNone(held.embedded())
        self.assertEqual(held.pointer("t")["bytes"], handover.HANDLED_EMBED_MAX + len('{"checkpoint": {"offset": 5}}'))
        held.release(keep_copy=False)

    def test_a_decoder_that_gives_up_means_a_pointer_not_an_error(self):
        real = json.loads

        def giving_up(raw, *args, **kwargs):
            if b'"pending"' in (raw if isinstance(raw, bytes) else raw.encode()):
                raise RecursionError("maximum recursion depth exceeded while decoding")
            return real(raw, *args, **kwargs)
        held = self.held('{"checkpoint": {"offset": 5}, "pending": {"open_work": []}}')
        with patch.object(handover.json, "loads", giving_up):
            self.assertIsNone(held.embedded())
        self.assertEqual(held.pointer("t")["checkpoint"], {"offset": 5})
        held.release(keep_copy=False)

    def test_receipt_content_that_is_not_valid_json_is_pointed_at_when_its_checkpoint_reads(self):
        held = self.held('{"checkpoint": {"offset": 5}, "pending": [')  # the checkpoint is whole, the rest is cut off
        self.assertIsNone(held.embedded())
        self.assertEqual(held.pointer("t")["checkpoint"], {"offset": 5})
        held.release(keep_copy=False)

    def test_a_receipt_with_two_checkpoints_is_described_by_the_one_the_readers_use(self):
        held = self.held('{"checkpoint": {"offset": 5}, "pending": {}, "checkpoint": {"offset": 99}}')
        self.assertIsNone(held.embedded())  # the decoder would keep the second; the readers take the first
        self.assertEqual(held.pointer("t")["checkpoint"], {"offset": 5})
        held.release(keep_copy=False)

    def test_pointer_hash_checkpoint_and_copy_come_from_one_read_of_the_receipt(self):
        def receipt(offset):
            return json.dumps({"checkpoint": {"offset": offset}, "pending": {"open_work": ["x" * 70000]}}, separators=COMPACT)
        self.path.write_text(receipt(1))
        first = self.path.read_bytes()
        replaced = []
        real_open, real_path_open = builtins.open, pathlib.Path.open

        def concurrent_acknowledgement(target):
            if str(target) == str(self.path) and not replaced:
                replaced.append(True)
                newer = self.path.with_name("newer.tmp")
                newer.write_text(receipt(2))
                os.replace(newer, self.path)  # lands right after the first open of the receipt

        def open_then_replace(file, *args, **kwargs):
            handle = real_open(file, *args, **kwargs)
            concurrent_acknowledgement(file)
            return handle

        def path_open_then_replace(path, *args, **kwargs):
            handle = real_path_open(path, *args, **kwargs)
            concurrent_acknowledgement(path)
            return handle

        with patch("builtins.open", open_then_replace), patch.object(pathlib.Path, "open", path_open_then_replace):
            held = handover.HandledReceipt(self.path, self.root)
        account = held.pointer("t")
        self.assertEqual((account["checkpoint"], account["bytes"], account["sha256"]),
                         ({"offset": 1}, len(first), hashlib.sha256(first).hexdigest()))
        self.assertEqual((self.root / "t.handled").read_bytes(), first)  # the copy is the version that was hashed
        held.release(keep_copy=True)

    def test_the_receipt_is_read_once_in_bounded_chunks(self):
        with self.path.open("wb") as stream:  # 3 MiB: a valid head, then bytes that are only hashed and copied
            stream.write(b'{"checkpoint": {"offset": 5}, "pending": ')
            stream.truncate(3 * 1024 * 1024)
        log, spy = spy_on_reads(self.path)
        with spy:
            held = handover.HandledReceipt(self.path, self.root)
        self.assertEqual(sum(got for _, got in log), 3 * 1024 * 1024)  # every byte once
        self.assertTrue(all(0 < asked <= 1 << 20 for asked, _ in log), log)  # no unbounded read
        self.assertEqual(held.pointer("t")["bytes"], 3 * 1024 * 1024)
        held.release(keep_copy=False)

    def test_release_leaves_nothing_behind_unless_the_copy_is_kept(self):
        held = self.held('{"checkpoint": {"offset": 5}}')
        self.assertEqual(len(self.kept()), 1)  # the temporary copy, while the receipt is held
        held.release(keep_copy=False)
        self.assertEqual(self.kept(), [])
        held = self.held('{"checkpoint": {"offset": 5}}')
        held.pointer("t")
        held.release(keep_copy=False)  # the note was refused: the published copy goes too
        self.assertEqual(self.kept(), [])

    def test_a_failure_while_publishing_the_copy_leaves_no_orphan(self):
        held = self.held('{"checkpoint": {"offset": 5}}')
        with patch.object(handover, "sync_directory", side_effect=OSError("disk went away")):
            with self.assertRaisesRegex(OSError, "disk went away"):
                held.pointer("t")
        held.release(keep_copy=False)  # what create does on its way out
        self.assertEqual(self.kept(), [])

    def test_a_receipt_with_no_readable_checkpoint_is_refused_and_keeps_nothing(self):
        for text in ("{not json", "[1, 2]", '{"pending": 1}'):
            held = self.held(text)
            self.assertIsNone(held.embedded())
            with self.assertRaisesRegex(ValueError, "invalid handled checkpoint"):
                held.pointer("t")
            held.release(keep_copy=False)
            self.assertEqual(self.kept(), [], text)

    def test_a_receipt_that_is_not_a_regular_file_is_refused(self):
        link = self.folder / "link.json"
        self.path.write_text("{}")
        link.symlink_to(self.path)
        with self.assertRaisesRegex(ValueError, "not a symlink"):
            handover.HandledReceipt(link, self.root)
        self.assertEqual(self.kept(), [])



if __name__ == "__main__":
    unittest.main()
