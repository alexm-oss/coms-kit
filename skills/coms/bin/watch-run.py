#!/usr/bin/env python3
"""Bounded watch ownership and signal cleanup, including task-owned descendants."""
import argparse
import fcntl
import os
import pathlib
import signal
import subprocess
import sys
import time


def signal_group(process, sig):
    try:
        os.killpg(process.pid, sig)
    except ProcessLookupError:
        pass
    except PermissionError:
        # macOS answers EPERM, not ESRCH, for a group that holds only zombies: nothing is left to
        # stop once the leader has exited. A leader that still runs and cannot be signalled is real.
        if process.poll() is None:
            raise


def stop_all(children):
    # One supervisor owns each direct group. Cleanup has one shared deadline.
    for process in children:
        signal_group(process, signal.SIGTERM)
    deadline = time.monotonic() + 3
    for process in children:
        try:
            process.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            pass
    for process in children:
        signal_group(process, signal.SIGKILL)
        process.wait()


def commands(directory):
    result = [['bash', str(directory / 'inbox-digest.sh')]]
    for extra in os.environ.get('WATCH_EXTRA', '').split(':'):
        if extra:
            result.append(['python3' if extra.endswith('.py') else 'bash', extra])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('once', 'all'))
    args = parser.parse_args()
    if not os.environ.get('COMS_HOLDER'):
        parser.error('set COMS_HOLDER explicitly')
    children = []

    def interrupted(_signum, _frame):
        raise KeyboardInterrupt

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, interrupted)
    try:
        subprocess.run(['coms', 'identity-check'], check=True)
        directory = pathlib.Path(__file__).resolve().parent
        if args.mode == 'all':
            for command in commands(directory):
                children.append(subprocess.Popen(command, start_new_session=True))
            while True:
                for child in children:
                    if child.poll() is not None:
                        print('watch-all: watcher exited with code', child.returncode, flush=True)
                        return child.returncode or 1
                time.sleep(0.1)
        state = pathlib.Path(os.environ.get('COMS_WATCH_DIR', os.environ.get('XDG_STATE_HOME',
                             str(pathlib.Path.home() / '.local/state')) + '/coms-orchestrator'))
        state = state / os.environ['COMS_HOLDER']
        if any(part.is_symlink() for part in (state, *state.parents)):
            raise ValueError('watch state must not contain symlinks')
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
        state.chmod(0o700)
        lock_path = state / 'watch-once.lock'
        lock_fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        os.fchmod(lock_fd, 0o600)
        with os.fdopen(lock_fd, 'a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print('watch-once: this holder already has a watcher', file=sys.stderr)
                return 75
            maximum = float(os.environ.get('WATCH_MAX_S', '7200'))
            settle = float(os.environ.get('WATCH_SETTLE_S', '8'))
            if not 0 < maximum <= 43200 or not 0 <= settle <= 60:
                raise ValueError('invalid watch duration')
            output = pathlib.Path(os.environ.get('WATCH_OUT', str(state / 'watch-once.out')))
            if any(part.is_symlink() for part in (output, *output.parents)):
                raise ValueError('watch output must not contain symlinks')
            receipt = state / 'watch-delivery.json'
            if receipt.exists():
                receipt.unlink()
            env = dict(os.environ, INBOX_MODE=os.environ.get('INBOX_MODE', 'night'),
                       INBOX_PEEK='1', INBOX_DELIVERY_RECEIPT=str(receipt))
            deadline = time.monotonic() + maximum
            output_fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
            os.fchmod(output_fd, 0o600)
            with os.fdopen(output_fd, 'w') as target:
                for command in commands(directory):
                    children.append(subprocess.Popen(command, env=env, stdout=target,
                                    stderr=subprocess.STDOUT, start_new_session=True))
                while time.monotonic() < deadline and all(p.poll() is None for p in children):
                    if output.stat().st_size:
                        time.sleep(settle)
                        break
                    time.sleep(0.1)
                stop_all(children)
                children.clear()
                target.flush()
                text = output.read_text()
                if text:
                    print(text, end='', flush=True)
                    if receipt.exists():
                        subprocess.run(['coms', 'checkpoint-deliver', str(receipt)], check=True)
                else:
                    duration = (str(maximum) + ' seconds' if maximum < 60 else str(int(maximum / 60)) + ' minutes')
                    print(time.strftime('%H:%M') + ' quiet: no event in ' + duration
                          + '. Start the watch again.', flush=True)
                return 0
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError, subprocess.SubprocessError):
        print('watch: setup or execution failed', file=sys.stderr)
        return 2
    finally:
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(sig, signal.SIG_IGN)
        stop_all(children)


if __name__ == '__main__':
    sys.exit(main())
