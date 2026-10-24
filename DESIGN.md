# Blustream SW42DA integration: design

Reference: Blustream SW42DA manual rev A7, "RS-232 Configuration and Telnet
Commands" (pp. 29–32). Firmware tested: MCU V1.9.0, Web 1.6.0.

## Device model

Four HDMI inputs, ARC as a fifth source, two HDMI outputs. Both outputs carry
the same source; output 2 adds a scaler. Source selection is therefore one
radio-button choice per unit.

## Control protocol (TCP 8000)

- Raw TCP, default port 8000 (telnet on 23 has the same command set). No
  login, no banner.
- Commands are ASCII terminated by `\r`. The device echoes the command,
  sends CRLF-terminated response lines, then the prompt `SW42DA>` with no
  line ending.
- Set commands reply `[SUCCESS]...` or `[ERROR]...`.
- **All output goes to every open connection**, including responses to other
  clients and the messages below.

### Commands used

| Function | Command |
|---|---|
| Select source | `OUT FR yy` (01–04 HDMI, 05 ARC) |
| Output enable | `OUT xx ON` / `OUT xx OFF` (01, 02) |
| Output 2 scaling | `OUT 02 SCALING yy` (01 Auto, 02 Force 1080p, 03 Bypass) |
| Power | `PON` / `POFF` |
| Display always on | `LCD ON` / `LCD OFF` (off: display off after 30 s unused) |
| Power LED follows display | `PWLED FOLLOW ON` / `PWLED FOLLOW OFF` |
| Read state | `STATUS` |

`HELP` on the device lists the full command set. There are no commands for
input labels.

### Unsolicited messages

| Message | Cause |
|---|---|
| `[SUCCESS]Set output from input NN.` | Source changed by front panel, Web-GUI or any client |
| `[SUCCESS]Set system power off.` | Standby (front-panel button or `POFF`) |
| `[SUCCESS]Set system power on, please wait a moment...` | Power on |
| `out fr N` | Command sent by the Web-GUI (lowercase, unpadded, no prompt) |
| Start-up banner, then `SW42DA> Source Signal: ...` | Device starting after power on |

Messages often arrive on the same line as a preceding prompt, e.g.
`SW42DA>[SUCCESS]Set output from input 01.`

### Standby

- `STATUS` works and reports `Power Off`; the selected source is retained.
- `OUT FR` is refused: `[ERROR]System is power off, please turn it on first.`
- After `PON`, `OUT FR` is accepted within ~1.3 s.
- For several seconds after `PON`, `STATUS` replaces the network table with
  `Network Loading...`.

### `STATUS` format

Blocks separated by blank lines, framed by lines of `=`. Each block is a
header row followed by value rows, aligned in space-padded columns. Values
and column names can contain single spaces (`Downmix 2CH`, `Master Output`),
so rows are sliced at the header's column offsets. Example:
`tests/fixtures/status_raw.txt`.

| Block (first column) | Columns used |
|---|---|
| `Power` | `Power`, `LCD`, `PWLED_Follow`, `Temp(C)` |
| `Input` | `HDMIcon` (rows 01–04) |
| `Output` | `FromIn` (current source), `HDMIcon`, `OutputEn`, `OutputScaler` (rows 01–02) |
| `Telnet` | `Mac`, `Local` (hostname); absent while `Network Loading...` |

Firmware version comes from the `FW Version:` header line.

## Web-GUI HTTP interface (port 80)

The Web-GUI POSTs JSON to `/cgi-bin/instr`. Reads need no login.

| Request `comhead` | Response fields |
|---|---|
| `get_input` | `name` (input labels), `cable`, `edid` |
| `get_control` | `i_name`, `o_name`, `o_source` (0-based), volumes, mutes |
| `get_settings` | MAC, network, versions, `hostname`, `lcd`, `pwled_follow` |

Example: `{"comhead":"get_input","language":1}` →
`{"comhead":"get_input","name":["INPUT1","INPUT2","INPUT3","INPUT4"],...}`

Web-GUI default login: `blustream` / `@Bls12345` (the manual says
`@Bls1234`).

## Discovery

Neither DHCP nor mDNS identifies the model, so both only produce candidates:

| Trigger | Matcher | Notes |
|---|---|---|
| DHCP | MAC `34D0B82*` (IEEE MA-M block of Blustream Pty Ltd) | Any hostname. Also matches other Blustream products and the unit's own Dante modules. `registered_devices` tracks address changes of configured units. |
| Zeroconf | `_http._tcp` named `sw42da*` | Default hostname only (`NET DNS` changes the advertised name). No model or MAC in TXT; control port not advertised. |

Candidates are identified with `get_settings`: `device_name` is the model and
is unaffected by hostname changes. Anything else aborts silently
(`not_supported`). The reply also provides the unique ID (`mac_addr`) and the
control port (`tcpipport`); `tcpip_enable: 0` aborts with `control_disabled`.
A DHCP candidate whose MAC is already configured only updates the host, with
no request.

`GET /cgi-bin/query` is not usable for this: it returns the Web-GUI's last
response, not current state.

## Integration structure

- `protocol.py`: asyncio client and `STATUS` parser, no Home Assistant
  imports. One persistent connection; a single reader task handles all
  input.
  - A response starts at the echo of its command and ends at the next prompt.
    Output before the echo belongs to another client and is ignored.
  - `STATUS` completes only after its closing `=` line, so a stray prompt
    cannot truncate it.
  - Source and power messages invoke callbacks wherever they appear.
  - `[ERROR]` raises `SW42DACommandError`. Commands are serialised.
  - `set_source(..., power_on=True)`: if refused in standby, sends `PON` and
    retries every 0.5 s for up to 15 s.
- `webgui.py`: `get_input` (labels) and `get_settings` (identity, control
  port).
- `coordinator.py`: polls `STATUS` and labels every 30 s, refreshes after
  each command, applies pushed source/power changes immediately. Label
  failures keep the previous labels. A dropped connection triggers an
  immediate reconnect attempt; entities are unavailable until it succeeds.
- `config_flow.py`: user and reconfigure steps validate with `STATUS` on the
  given port; DHCP and zeroconf steps identify with `get_settings`.
- `__init__.py`: setup and unload only. Nothing is written to the device
  unless the user operates an entity.

### Entities

| Entity | Platform | Source of state | Command |
|---|---|---|---|
| Source | select | `FromIn` | `OUT FR yy` (powers on from standby) |
| HDMI 1–4, ARC | switch | power on and `FromIn` = N | `OUT FR yy` on turn-on (powers on from standby) |
| HDMI output 1, 2 | switch | `OutputEn` | `OUT 0N ON/OFF` |
| HDMI output 2 scaling | select | `OutputScaler` | `OUT 02 SCALING yy` |
| Power | switch | `Power` | `PON` / `POFF` |
| Display always on | switch (config) | `LCD` | `LCD ON/OFF` |
| Power LED follows display | switch (config) | `PWLED_Follow` | `PWLED FOLLOW ON/OFF` |
| Input/output connected | binary_sensor | `HDMIcon` | – |
| Temperature | sensor | `Temp(C)` | – |

The source switches exist for controllers that show on/off state, such as
Stream Deck buttons via Companion. Their entity IDs are fixed
(`switch.blustream_sw42da_source_hdmi_N`); their names follow the Web-GUI
labels. Home Assistant prefixes friendly names with the device name, so the
bare label is also exposed as the `label` attribute, which Companion reads as
`entity.<entity_id>.attributes.label`.

## Open questions

- Whether IR remote source changes produce
  `[SUCCESS]Set output from input NN.` If not, they are picked up by polling.
