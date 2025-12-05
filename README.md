# Home Assitant Keyboard Shortcuts Helper (headless)

A minimal helper that binds keyboard shortcuts to Home Assistant REST API calls. Built to run on Linux/macOS systems: no UI, just a tiny Python script and a YAML config.

License: MIT (see LICENSE).

## How it works (Home Assistant REST API)
- Maps global accelerators to Home Assistant REST endpoints (`/api/services/...`, `/api/states/...`, etc.).
- Stores server config (protocol/host/port/token) plus shortcuts with method, endpoint, and optional JSON body.
- Uses the standard REST auth header (`Authorization: Bearer <token>`) for every call.
- When a hotkey fires, it sends the REST request and prints the response to stdout for quick debugging.

### REST API examples (from HA docs)
- Toggle a light via the services endpoint:
  ```sh
  curl -X POST \\
       -H "Authorization: Bearer YOUR_TOKEN" \\
       -H "Content-Type: application/json" \\
       -d '{"entity_id": "light.living_room"}' \\
       http://homeassistant.local:8123/api/services/light/toggle
  ```
- Get the state of an entity:
  ```sh
  curl -H "Authorization: Bearer YOUR_TOKEN" \\
       http://homeassistant.local:8123/api/states/sensor.outdoor_temperature
  ```
Replace the URL/token/entity_id with your own; the script uses the same REST calls under the hood.

## Files
- `ha_shortcuts.py` — CLI runner that loads `config.yaml`, triggers HA REST calls, and (optionally) registers global hotkeys using the `keyboard` library.
- `config.yaml.example` — template with example shortcuts (copy to `config.yaml` and fill in your token/hosts). Your real `config.yaml` is gitignored.
- `requirements.txt` — `requests` + `keyboard` + `pynput` (plus `pyinstaller` for building binaries).

## Install (e.g., on Debian/Ubuntu/Raspberry Pi OS)
```sh
sudo apt-get update
sudo apt-get install -y python3 python3-pip
cd /path/to/ha-keyboard-shortcuts
pip3 install -r requirements.txt
```
The `keyboard` library needs access to `/dev/input` on Linux; run with sudo or grant the user read access to the input device.

## Usage
List shortcuts:
```sh
python ha_shortcuts.py --list
```
Trigger one directly (debug/test without listening):
```sh
python ha_shortcuts.py --trigger table_led
```
Listen for hotkeys (blocks; Ctrl+C to exit):
```sh
sudo python ha_shortcuts.py --listen
```
If no flag is provided, `--listen` is assumed. Edit `config.yaml` to add more shortcuts or adjust the entity ID from the screenshot to match your script name exactly.

### macOS note
The default `keyboard` backend has limited support on macOS/Python 3.13. If you see a key mapping error, use the pynput backend (allow Accessibility when prompted):
```sh
.venv/bin/python ha_shortcuts.py --listen --backend pynput
```
Run in background and stop it with built-in flags:
```sh
.venv/bin/python ha_shortcuts.py --background --backend pynput
.venv/bin/python ha_shortcuts.py --stop
```
Logs go to `ha_shortcuts.out`; PID is stored in `ha_shortcuts.pid`.

## Config format (`config.yaml`)
```yaml
server:
  base_url: http://homeassistant.local:8123
  token: <long-lived token>
shortcuts:
  - name: table_led
    hotkey: ctrl+alt+l
    method: POST
    endpoint: /api/services/script/turn_on
    body:
      entity_id: switch.table_lights
```
- `hotkey` strings follow the [`keyboard` syntax](https://keyboard.readthedocs.io/en/latest/shortcuts.html), e.g., `ctrl+shift+f`, `alt+space`.
- Add more entries under `shortcuts` to map additional HA actions.
- Responses are printed to stdout with status and body text for quick debugging.

## Notes
- The provided token is stored in plain text for convenience; rotate it if you check this directory into source control.
- The script mirrors the Home Assistant REST examples: `Authorization: Bearer <token>`, JSON body for service calls, and simple success detection (`response.ok`).
- If `keyboard` cannot be imported (or lacks permissions), you can still call `--trigger` for manual testing.

## Build your own binary
- macOS: `./.venv/bin/pyinstaller --onefile --name ha-shortcuts --exclude-module tkinter ha_shortcuts.py`
- Linux: run the same PyInstaller command on a Linux host/VM (cross-compiling from macOS to Linux isn’t supported). Use `--backend keyboard` when running if `/dev/input` permissions allow.
- Place `config.yaml` next to the binary (or pass `--config /path/to/config.yaml`). Allow Accessibility for hotkeys on macOS.

## Run in background (manual start/stop)
From the repo root:
```sh
nohup .venv/bin/python ha_shortcuts.py --listen --backend pynput > ha_shortcuts.out 2>&1 &
echo $! > ha_shortcuts.pid
```
- On Linux, you can switch `--backend keyboard` and drop `sudo` if `/dev/input` permissions allow.
- The PID is stored in `ha_shortcuts.pid`; stop with:
  ```sh
  kill $(cat ha_shortcuts.pid)
  rm ha_shortcuts.pid
  ```
- Logs are written to `ha_shortcuts.out`.
