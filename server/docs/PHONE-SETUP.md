# Phone setup

Goal: the phone reaches `https://127.0.0.1:8445` on its own loopback, the traffic lands on this PC,
and the game server terminates TLS with a certificate whose hostname matches.

## 1. Prepare the host

```bash
python -m dittobuffer            # starts server + admin
python -m dittobuffer /status    # both should be ● up
```

The family profile uses `config/tls/server.pem` and `config/tls/server-key.pem`. Regenerate them with:

```bash
python scripts/create-family-tls.py --host 127.0.0.1 --directory config/tls
```

`127.0.0.1` is deliberate: the certificate must have an IP SAN for the loopback address the client dials.

## 2. Connect the phone

```bash
adb devices -l       # inventory only; accept the debugging prompt on the phone
# PowerShell: choose your actual development phone explicitly.
$env:DITTOBUFFER_ADB_SERIAL = 'YOUR_ADB_SERIAL'
python -m dittobuffer /reconnect
```

`/reconnect` performs, in order:

1. Selects the configured serial, or the sole connected physical phone when no serial is configured. Multiple phones require an explicit choice. A selected phone disappearing never selects a different phone.
2. Runs `adb -s SERIAL reverse tcp:8445 tcp:8445` and `tcp:8080`; other reverse rules remain intact.
3. Starts this checkout's gnirehtet with the same explicit serial if its port is free. An unrelated relay/listener is left untouched and reported.
4. Uses `adb -s SERIAL` to force-stop and relaunch **only** `org.librenlp.pokemongo.dev`; no app data is cleared.

In Bash use `export DITTOBUFFER_ADB_SERIAL=YOUR_ADB_SERIAL`. The Windows launcher also accepts `START-SERVER.bat YOUR_ADB_SERIAL`. Configure a serial when another device may connect later. The console never resets the global ADB server or kills processes by name or listening port.

Verify from the phone, not from the PC:

```bash
adb -s YOUR_ADB_SERIAL shell 'echo | nc -w 5 127.0.0.1 8445; echo rc=$?'
# rc=0 means the tunnel works
```

## 3. Install the patched client

```bash
adb -s YOUR_ADB_SERIAL install -r apk/built/dittobuffer-family-127.0.0.1.apk
adb -s YOUR_ADB_SERIAL shell am start -n org.librenlp.pokemongo.dev/org.dittobuffer.launcher.FamilyActivity
```

The launcher shows the server origin, a sign-in form and **Continue saved account**.

## Why the tunnel keeps dying

`adb reverse` rules belong to the ADB server session and to the current USB configuration. Any of the
following silently removes them:

- switching USB mode (charging ↔ file transfer ↔ tethering)
- unplugging the cable
- `adb kill-server`, `adb start-server`, or restarting the ADB daemon
- some OEM "USB debugging" toggles

The server keeps running the whole time, which is why the client reports *failed to connect to
https://127.0.0.1:8445* while `/status` still shows `server ● up`.

Fix: `python -m dittobuffer /reconnect` (or `adb -s YOUR_ADB_SERIAL reverse tcp:8445 tcp:8445`).

## gnirehtet (optional, no Wi-Fi needed)

`tools/gnirehtet/` ships the Windows relay and the Android client. `/restart gnirehtet` starts it; it
opens `127.0.0.1:31416` and installs its VPN on the phone. The ADB reverse rules work independently,
so the game stays reachable even if the relay is down.

Service PID records now include the checkout, command and process-lifetime identity. `/stop` and `/restart` refuse to terminate an unverified or reused PID. Legacy numeric PID files are insufficient proof of ownership: stop those older services manually if needed. No process is adopted merely because its port is open. Existing app data, the official installation and other checkouts are preserved.

## Accounts

| Mode | Config | Behaviour |
|---|---|---|
| `family` + `open` | `config/family.json` | Anyone can register; invitation optional |
| `family` + `invite` | `authentication.registration = "invite"` | Requires an invitation code |
| local | `config/development.json` | Test identity, no credentials |

Create the first account from the launcher's **Create invited account** form.
