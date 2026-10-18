# Blustream SW42DA for Home Assistant

Local control of the Blustream SW42DA (4-input HDMI switcher with Dante audio)
over its TCP control port.

## Features

- Source selection (HDMI 1–4, ARC). Both HDMI outputs always carry the same
  source.
- One switch per source, behaving as radio buttons, for controllers that show
  on/off state (e.g. Stream Deck via Bitfocus Companion).
- Selecting a source in standby powers the unit on first.
- Source and power changes made on the front panel or in the Web-GUI are
  pushed immediately; everything else is polled every 30 s.
- Input labels from the Web-GUI.
- HDMI output enable, output 2 scaling, power, display and power-LED settings.
- HDMI connection sensors and temperature.
- Discovery by DHCP (Blustream MAC block, any hostname) and zeroconf.
  Candidates are confirmed as an SW42DA over HTTP before they are offered.

## Requirements

- Home Assistant 2026.10 or later.
- TCP/IP control enabled on the unit (Web-GUI → Settings → Device Status;
  on by default, port 8000).

## Installation

**HACS:** search for **Blustream SW42DA**, install, restart Home Assistant.
Until it is listed, add `https://github.com/oblongular/blustream_sw42da` as a
custom repository (category *Integration*) first.

**Manual:** copy `custom_components/blustream_sw42da` to
`<config>/custom_components/` and restart Home Assistant.

## Setup

Discovered units appear under **Settings → Devices & services**. To add one
manually, use **Add integration → Blustream SW42DA** with the host
(`sw42da.local` by default) and port (8000). Use **Reconfigure** to change
them later.

When a unit is first added, the integration sets the display and power LED to
stay on (see below).

## Entities

| Entity | Type | Description |
|---|---|---|
| Source | select | HDMI 1–4 or ARC. Attribute `labels`: Web-GUI label per option. |
| *Label* (HDMI 1–4) | switch | On while that input is selected and the unit is on. Turn on to select it; turn off does nothing. Named after the Web-GUI label; attributes `label`, `input`. Entity ID `switch.blustream_sw42da_source_hdmi_N`. |
| Source ARC | switch | As above, for ARC. Disabled by default. |
| HDMI output 1, 2 | switch | Output enable. |
| HDMI output 2 scaling | select | Auto, Force 1080p, Bypass. |
| Power | switch | On / standby. |
| Display always on | switch (config) | Off: display turns off after 30 s unused. |
| Power LED follows display | switch (config) | On: power LED turns off with the display. |
| HDMI input/output *N* connected | binary_sensor (diagnostic) | Cable connected. |
| Temperature | sensor (diagnostic) | Internal temperature. |

## Stream Deck with Bitfocus Companion

Using Companion's Home Assistant module, for each input button:

- **Action:** switch on `switch.blustream_sw42da_source_hdmi_N`
- **Feedback:** switch state of the same entity is on → background colour
- **Text:** `$(<connection>:entity.switch.blustream_sw42da_source_hdmi_N.attributes.label)`

One button is lit for the current input; none in standby.

### Input labels as button text

Rename inputs in the unit's Web-GUI (Input page). Home Assistant picks up the
new names within 30 s, and Companion's Home Assistant module exposes them as
variables:

```
$(<connection>:entity.switch.blustream_sw42da_source_hdmi_N.attributes.label)
```

`<connection>` is the label of the Home Assistant connection on Companion's
Connections tab. Typing `$(` in the button text field opens a variable picker;
search for `hdmi_N` and choose the `label` attribute.

The module builds its variable list on connect. If the `label` variables are
missing, disable and re-enable the connection.

The unit's front-panel display keeps its built-in input names; Web-GUI labels
are not shown there.

## Device notes

- Every connected control client receives all device output, so other
  control systems or a telnet session can stay connected.
- Inputs are renamed in the Web-GUI (Input page). Default login
  `blustream` / `@Bls12345` (the manual's `@Bls1234` is wrong).

## Development

Protocol and design: [DESIGN.md](DESIGN.md).

```sh
nix-shell                     # or any Python 3.14 environment
uv venv && uv pip install -r requirements_test.txt
pytest
python scripts/sw42da.py HOST status|send CMD...|listen
```

## License

MIT. See [LICENSE](LICENSE).
