# Troubleshooting

## The client says "saved account unavailable: failed to connect to https://127.0.0.1:8445"

The server is fine; the phone's tunnel is gone.

```bash
python -m dittobuffer /status      # server ● up?
python -m dittobuffer /reconnect   # rebuild reverse + relay + relaunch
```

If `/status` shows no device, the cable or the USB mode is the problem: set the phone to **File
transfer** *and* re-authorise USB debugging. Then run `/reconnect` again.

Confirm from the phone itself:

```bash
adb shell 'echo | nc -w 5 127.0.0.1 8445; echo rc=$?'
```

`rc=0` means the TCP path works and any remaining error is TLS or credentials.

## TLS errors

```
CERTIFICATE_VERIFY_FAILED / hostname mismatch
```

The certificate must contain an IP SAN for the exact address the client dials (`127.0.0.1` for ADB
reverse). Regenerate:

```bash
python scripts/create-family-tls.py --host 127.0.0.1 --directory config/tls-new
cp config/tls-new/*.pem config/tls/
python -m dittobuffer /restart server
```

## Admin panel is blank

- Check `/status`: `admin ● up` on port 8080.
- Open <http://127.0.0.1:8080/api/status>; a JSON error means the panel is running but the database
  path in `config/family.json` is wrong.
- PokeAPI sprites and OSM tiles need internet; the panel still works offline with cached names.

## Spawns or gyms do not appear in game

- `/spawns` and `/pois` show what the admin wrote.
- Restart the game server so the world overlay reloads: `/restart server`.
- Gyms and stops live in `data/world/admin-pois.json`; the published snapshot is never rewritten.
- A spawn further than `interaction_range_meters` from the player is visible on the map but cannot be
  encountered.

## Streets are blank outside the Netherlands

The checked-in world snapshot only covers its own extract. Outside it the server falls back to
per-area OpenStreetMap cells in `data/maps/areas/`.

- With `world.osm_download` on, the cell under the trainer downloads before the map reply and the
  surrounding cells follow in the background; give it a few seconds and reload the map.
- `GET /api/osm` (admin panel: **Streets & map data**) shows the toggle, radius, cached cell count and
  the newest download time.
- A failed cell is retried after 15 minutes. Check the server log for `OSM area ... unavailable`.
- Gyms, stops, spawns and raids still render while a cell is empty.
- Tiles are advertised on `world.map_tiles_base` (default the plain-HTTP admin origin
  `http://127.0.0.1:8080/map/tiles`), because the phone's map loader does not trust the TLS game port.
  Both 8445 and 8080 must be in `adb reverse --list`.
- If the server never logs a tile request, the map loader is failing before HTTP. See
  [`KNOWN-ISSUES.md`](KNOWN-ISSUES.md) and the Frida `Local map request` console line.

## Patcher fails

| Message | Fix |
|---|---|
| `Java runtime not found` | Install JDK 17+, put `java` on `PATH` or set `JAVA_HOME` |
| `Missing build tool: zipalign` | Restore `tools/android-build-tools/` |
| `apktool` decode error | The APK is protected or split-only; use the official base APK |
| signature verification fails | Delete the output and re-run; the bundled key is used automatically |

## Server will not start

```bash
python -m dittobuffer /logs server 60
```

| Log line | Cause |
|---|---|
| `Address already in use` | Another copy is running: `/stop server` |
| `FileNotFoundError ... gmts.binpb.gz` | `data/static/` is missing |
| `Configuration must stay inside the project` | `--config` points outside the repository |
| `No such file ... world/current.json` | World snapshot not present in `data/world/` |

## Ports

| Port | Owner |
|---|---|
| 8445 | game server (TLS) |
| 8080 | admin panel |
| 31416 | gnirehtet relay |
| 27042 | Frida Gadget listen socket |
