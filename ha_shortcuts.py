#!/usr/bin/env python3
"""Simple Home Assistant REST shortcut runner.

Headless helper for lightweight Linux/macOS systems that don't need many resources. It:
- reads a small YAML/JSON config (server + shortcuts)
- optionally registers global hotkeys via the "keyboard"/"pynput" backends
- can trigger a shortcut directly for debugging/testing

Usage examples:
  python ha_shortcuts.py --list
  sudo python ha_shortcuts.py --listen
  python ha_shortcuts.py --trigger table_led
See https://developers.home-assistant.io/docs/api/rest/ for REST details.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

import requests
import yaml

try:
    import keyboard  # type: ignore
except Exception:  # ImportError or permissions issues
    keyboard = None

try:
    from pynput import keyboard as pynput_keyboard  # type: ignore
except Exception:
    pynput_keyboard = None

DEFAULT_CONFIG_PATH = Path(__file__).with_name("config.yaml")
PID_FILE = Path(__file__).with_name("ha_shortcuts.pid")
LOG_FILE = Path(__file__).with_name("ha_shortcuts.out")


class ConfigError(RuntimeError):
    pass


def load_config(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    try:
        if path.suffix.lower() in {".yaml", ".yml"}:
            return yaml.safe_load(path.read_text())
        return json.loads(path.read_text())
    except (yaml.YAMLError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Config parse error: {exc}") from exc


def validate_config(cfg: Dict[str, Any]) -> None:
    if "server" not in cfg or "shortcuts" not in cfg:
        raise ConfigError("Config must contain 'server' and 'shortcuts'")

    server = cfg["server"]
    if "base_url" not in server or "token" not in server:
        raise ConfigError("Server config must include 'base_url' and 'token'")

    if not isinstance(cfg["shortcuts"], list):
        raise ConfigError("'shortcuts' must be a list")

    for action in cfg["shortcuts"]:
        for field in ("name", "method", "endpoint"):
            if field not in action:
                raise ConfigError(f"Shortcut missing '{field}': {action}")
        action.setdefault("body", {})
        action.setdefault("hotkey", None)


def format_action(action: Dict[str, Any]) -> str:
    hotkey = action.get("hotkey") or "(no hotkey)"
    return f"{action['name']} -> {action['method']} {action['endpoint']} {hotkey}"


def send_request(server: Dict[str, Any], action: Dict[str, Any]) -> Dict[str, Any]:
    url = server["base_url"].rstrip("/") + action["endpoint"]
    method = action["method"].upper()
    body = action.get("body", {}) or None

    headers = {
        "Authorization": f"Bearer {server['token']}",
        "Content-Type": "application/json",
    }

    start = time.time()
    response = requests.request(method, url, headers=headers, json=body, timeout=15)
    elapsed_ms = int((time.time() - start) * 1000)
    return {
        "ok": response.ok,
        "status": response.status_code,
        "elapsed_ms": elapsed_ms,
        "text": response.text,
    }


def trigger_action(server: Dict[str, Any], action: Dict[str, Any], source: str) -> None:
    label = action["name"]
    print(f"[{source}] Triggering '{label}' -> {action['method']} {action['endpoint']}")
    try:
        result = send_request(server, action)
        status = "ok" if result["ok"] else "fail"
        print(f"[{source}] {label}: {status} ({result['status']}, {result['elapsed_ms']}ms)")
        if result["text"]:
            print(f"[{source}] Response: {result['text']}")
    except Exception as exc:  # network errors, etc.
        print(f"[{source}] {label}: error {exc}")


def register_hotkeys(server: Dict[str, Any], shortcuts: List[Dict[str, Any]], backend: str) -> None:
    if backend == "keyboard":
        listen_with_keyboard(server, shortcuts)
    elif backend == "pynput":
        listen_with_pynput(server, shortcuts)
    else:
        raise ConfigError(f"Unknown backend '{backend}'")


def listen_with_keyboard(server: Dict[str, Any], shortcuts: List[Dict[str, Any]]) -> None:
    if keyboard is None:
        raise ConfigError(
            "keyboard backend unavailable; install it and run with permissions to read /dev/input"
        )

    try:
        for action in shortcuts:
            hotkey = action.get("hotkey")
            if not hotkey:
                continue
            keyboard.add_hotkey(hotkey, lambda a=action: threading.Thread(
                target=trigger_action, args=(server, a, "hotkey"), daemon=True
            ).start())
            print(f"Registered hotkey '{hotkey}' for '{action['name']}'")
    except ValueError as exc:
        raise ConfigError(f"Failed to register hotkey '{hotkey}': {exc}") from exc

    print("Listening for hotkeys (Ctrl+C to exit)...")
    keyboard.wait()  # Blocks until interruption


def listen_with_pynput(server: Dict[str, Any], shortcuts: List[Dict[str, Any]]) -> None:
    if pynput_keyboard is None:
        raise ConfigError("pynput backend unavailable; install it with `pip install pynput`")

    hotkeys = []
    for action in shortcuts:
        hotkey = action.get("hotkey")
        if not hotkey:
            continue
        combo = _to_pynput_combo(hotkey)
        try:
            parsed = pynput_keyboard.HotKey.parse(combo)
        except ValueError as exc:
            raise ConfigError(f"Failed to parse hotkey '{hotkey}': {exc}") from exc

        def handler(act=action):
            threading.Thread(
                target=trigger_action, args=(server, act, "hotkey"), daemon=True
            ).start()

        hotkeys.append(pynput_keyboard.HotKey(parsed, handler))
        print(f"Registered (pynput) '{hotkey}' for '{action['name']}'")

    listener = None

    def on_press(key):
        for hk in hotkeys:
            hk.press(listener.canonical(key))  # type: ignore[attr-defined]

    def on_release(key):
        for hk in hotkeys:
            hk.release(listener.canonical(key))  # type: ignore[attr-defined]

    print("Listening for hotkeys via pynput (Ctrl+C to exit)...")
    listener = pynput_keyboard.Listener(on_press=on_press, on_release=on_release)
    listener.start()
    listener.join()


# Convert a user hotkey string like "ctrl+alt+l" into pynput format.
def _to_pynput_combo(hotkey: str) -> str:
    if not hotkey:
        raise ConfigError("Empty hotkey")

    modifiers = {
        "ctrl", "control", "alt", "option", "shift",
        "cmd", "command", "super", "win", "windows"
    }
    parts = []
    for raw in hotkey.split("+"):
        part = raw.strip().lower()
        if not part:
            continue
        if part in modifiers:
            parts.append(f"<{part}>")
        elif len(part) == 1:
            parts.append(part)
        else:
            parts.append(f"<{part}>")
    if not parts:
        raise ConfigError(f"Invalid hotkey '{hotkey}'")
    return "+".join(parts)


def start_background(args: argparse.Namespace) -> int:
    if PID_FILE.exists():
        pid = PID_FILE.read_text().strip()
        if pid and _is_running(int(pid)):
            print(f"Already running (pid {pid}). Stop first with --stop.")
            return 1
        PID_FILE.unlink(missing_ok=True)

    cmd = [
        sys.executable,
        "-u",  # unbuffered so logs flush immediately
        str(Path(__file__).resolve()),
        "--listen",
        "--backend",
        args.backend,
        "--config",
        str(args.config),
    ]
    stdout = LOG_FILE.open("a", buffering=1)
    stderr = LOG_FILE.open("a", buffering=1)
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=stdout,
            stderr=stderr,
            preexec_fn=os.setsid if os.name == "posix" else None,
            close_fds=True,
            env=env,
        )
    except Exception as exc:
        print(f"Failed to start background listener: {exc}")
        return 1

    PID_FILE.write_text(str(proc.pid))
    print(f"Started background listener (pid {proc.pid}). Logs: {LOG_FILE}")
    return 0


def stop_background() -> int:
    if not PID_FILE.exists():
        print("No pid file found. Nothing to stop.")
        return 1
    try:
        pid = int(PID_FILE.read_text().strip())
    except Exception:
        print("Could not read pid file.")
        PID_FILE.unlink(missing_ok=True)
        return 1

    try:
        os.kill(pid, signal.SIGTERM)
        print(f"Stopped process {pid}")
    except ProcessLookupError:
        print(f"No process {pid} found")
    except PermissionError:
        print(f"Permission denied to stop process {pid}")
        return 1
    finally:
        PID_FILE.unlink(missing_ok=True)
        LOG_FILE.unlink(missing_ok=True)
    return 0


def _is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False

def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description="Minimal Home Assistant shortcut runner")
    parser.add_argument("--config", "-c", type=Path, default=DEFAULT_CONFIG_PATH,
                        help="Path to config file (YAML or JSON, default: config.yaml)")
    parser.add_argument("--list", action="store_true", help="List configured shortcuts")
    parser.add_argument("--trigger", "-t", help="Trigger a shortcut by name and exit")
    parser.add_argument("--listen", action="store_true", help="Start hotkey listener")
    parser.add_argument("--backend", choices=["keyboard", "pynput"], default="keyboard",
                        help="Hotkey backend: 'keyboard' (Linux/headless) or 'pynput' (macOS friendly)")
    parser.add_argument("--background", action="store_true",
                        help="Run listener in background and write ha_shortcuts.pid")
    parser.add_argument("--stop", action="store_true",
                        help="Stop background listener using ha_shortcuts.pid")
    args = parser.parse_args(argv)

    # Background stop shortcut: no config load needed
    if args.stop:
        return stop_background()

    # Background start: spawn child and exit
    if args.background:
        return start_background(args)

    try:
        cfg = load_config(args.config)
        validate_config(cfg)
    except ConfigError as exc:
        print(f"Config error: {exc}")
        return 1

    server = cfg["server"]
    shortcuts: List[Dict[str, Any]] = cfg["shortcuts"]

    if args.list:
        print("Configured shortcuts:")
        for action in shortcuts:
            print(" - " + format_action(action))

    if args.trigger:
        matches = [a for a in shortcuts if a.get("name") == args.trigger]
        if not matches:
            print(f"No shortcut named '{args.trigger}'")
            return 1
        trigger_action(server, matches[0], "cli")
        return 0

    # Default to listening if no action requested
    if args.listen or (not args.list and not args.trigger):
        try:
            register_hotkeys(server, shortcuts, args.backend)
        except ConfigError as exc:
            print(exc)
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
