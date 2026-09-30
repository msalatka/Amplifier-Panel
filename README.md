# Amp Panel

Amp Panel reads device telemetry from `status.xml` and writes commands to
`control.xml`. Each `params_*` section in `status.xml` becomes a device profile.

## Installation

### Building the Debian package

Install the build tools on the target Debian system:

```bash
sudo apt update
sudo apt install build-essential debhelper git python3 python3-pip
```

Run the following command in the project directory:

```bash
./packaging/build_deb.sh
```

The `.deb` package will be created in the parent directory. Install it using the
generated file name:

```bash
sudo apt install ../amp-panel_*.deb
```

The installer creates the `amp-panel` system user, systemd services, a data
directory, and an initial configuration. The configuration can be changed at
any time with:

```bash
sudo amp-panel configure
```

This command opens the complete configuration file in `$VISUAL`, `$EDITOR`, or
the system `editor`, and validates it before applying any changes.

After installation, you can verify the system with:

```bash
sudo amp-panel doctor
sudo amp-panel status
```

By default, the panel is available at:

```text
http://amp-panel.local:8000
```

The host name and port depend on the installation configuration.

## Using the application

After signing in, select a device from the selector in the header. Each profile
has its own connection status, live data, and history.

### Live View

Displays the latest complete snapshot of the selected device. For amplifiers,
Administrators and Operators can choose which measurements appear on the main
screen. Viewers have read-only access.

### Control

The **Control** tab is available to Operators and Administrators. Its fields are
generated automatically from `xml_mapping.json`: every field marked with
`"writable": true` is displayed. When **Apply changes** is selected, the panel
writes only the values changed by the user to `control.xml`.

The request history at the bottom shows whether each command is pending,
applied, rejected, failed, or timed out. Add control fields through the mapping;
no GUI change is required.

### Overview and Statistics

- **Overview** displays a chart of historical values for the selected period.
- **Statistics** calculates statistics for the selected period.
- Historical data can be exported to CSV.

### Administration

Administrative functions include:

- **Access Control** — users, roles, local passwords, and account activity,
- **SNMP Configuration** — agent, community, and trap receiver,
- **Network Configuration** — current interface and IPv4 settings,
- **Time Diagnostics** — NTP synchronization status,
- **Service Diagnostics** — XML telemetry, database, and Syslog,
- **Edit Variables** — XML field mapping editor.

Network changes may interrupt the current connection to the panel. Apply them
only to the interface whose configuration is currently displayed.

## Authentication

The panel supports two modes selected through `amp-panel configure`:

- `local` — accounts and PBKDF2 hashes are stored locally,
- `radius` — an external RADIUS server verifies the password, while the panel
  stores roles and account activity status.

In RADIUS mode, the user must exist in both the panel configuration and the
RADIUS server. The repository includes a helper installer:

```bash
cd server_setup
sudo ./install_radius_server.sh
```

## Main configuration

The package configuration is stored in:

```text
/etc/amp-panel/amp-panel.env
```

Do not edit it while the service is running. Use:

```bash
sudo amp-panel configure
```

### File and directory locations

The Debian package uses the following locations by default:

| Location | Contents |
|---|---|
| `/usr/lib/amp-panel/` | Installed application code, templates, static files, Python dependencies, and the packaged default XML mapping |
| `/usr/bin/amp-panel` | Administration command available from the shell |
| `/etc/amp-panel/amp-panel.env` | Host-specific application configuration |
| `/var/lib/amp-panel/` | Persistent writable data: `status.xml`, `control.xml`, `xml_mapping.json`, `measurements.db`, and `persisted_state.json` |
| `/var/log/amp-panel/` | Application logs managed by rsyslog and logrotate |
| `/run/amp-panel/` | Temporary runtime files, including the network-agent socket |

Use the following command to display the paths used by the installed system:

```bash
sudo amp-panel paths
```

`AMP_PANEL_DATA_DIR` can be moved from `/var/lib/amp-panel` to an absolute path
under `/mnt`, `/media`, or `/srv`, for example when persistent data must be kept
on a separate disk. The database, persisted state, and `control.xml` must remain
inside the selected data directory. Keeping `xml_mapping.json` there allows the
panel to update it through the GUI. `status.xml` may be located elsewhere when
it is produced by an external device process.

The main XML settings are:

```ini
XML_STATUS_FILE=/var/lib/amp-panel/status.xml
XML_CONTROL_FILE=/var/lib/amp-panel/control.xml
XML_CONTROL_ACK_TIMEOUT_SECONDS=15
XML_MAPPING_FILE=/var/lib/amp-panel/xml_mapping.json
XML_POLL_SECONDS=2
XML_STALE_SECONDS=60
```

Other important settings are:

```ini
AMP_PANEL_PORT=8000
AMP_PANEL_DATA_DIR=/var/lib/amp-panel
DATABASE_FILE=/var/lib/amp-panel/measurements.db
PERSISTED_STATE_FILE=/var/lib/amp-panel/persisted_state.json
AUTH_MODE=local
SNMP_PORT=1161
```

`amp-panel configure` validates changes, prepares files and permissions, and
restarts the services.

## Telemetry: status.xml

`status.xml` is owned by the device process. The recommended update method is to
write a temporary file and atomically replace the active file. The panel reads
the document periodically but never writes to it.

Minimal structure:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<status>
  <module>
    <serial_number>DEVICE-001</serial_number>
    <firmware>1.0</firmware>
  </module>

  <params_oba3>
    <param id="5.1.1.1">
      <name>Gain</name>
      <value>30.0</value>
    </param>
    <param id="5.1.1.2">
      <name>GainSet</name>
      <value>28.5</value>
    </param>
  </params_oba3>
</status>
```

Each top-level `params_*` section is displayed and stored as an independent
profile. The default mapping recognizes:

| Profile | XML sections |
|---|---|
| `local` | `params_local` |
| `local_di` | `params_local_di` |
| `remote` | `params_remote` |
| `remote_di` | `params_remote_di` |
| `oba` | `params_oba` |
| `oba3` | `params_oba3` |

The selector follows the latest valid XML. Removing a section hides its profile
without deleting history or display settings. New sections appear as read-only
profiles until their fields are saved in `xml_mapping.json`.

The profile ID is the part after `params_`, unless an existing mapping assigns
that XML section a different section `key`. A section may also define optional
presentation metadata:

- `profile_label` — name shown in the device selector,
- `display_group` — profiles with the same value stay together; a line separates
  them from the next group,
- `view_profile` — `station` or `amplifier`, which selects the page layout,
- `order` — numeric position in the selector,
- `snmp_index` — stable number used in the profile connection-state OID.

Put these properties on the mapping entry when it contains one section, or on
individual sections when it contains several. Saving the first discovered field
of a new profile creates its mapping and assigns a stable SNMP index.

### Profile layout and amplifier diagram

`view_profile` selects the Live View layout:

- `"station"` displays the station layout without the amplifier diagram,
- `"amplifier"` displays the amplifier layout and diagram.

New profiles use `"station"` until configured in `xml_mapping.json`.

Example mapping for a new amplifier profile:

```json
{
  "new_amplifier": {
    "label": "New amplifier",
    "view_profile": "amplifier",
    "display_group": "Amplifiers",
    "order": 70,
    "snmp_index": 7,
    "sections": [
      {
        "key": "new_amplifier",
        "xml_section": "params_new_amplifier",
        "label": "New amplifier",
        "discover_unmapped": true,
        "fields": [
          {
            "key": "GainSet",
            "name": "GainSet",
            "label": "Gain setpoint",
            "type": "number",
            "unit": "dB",
            "writable": true,
            "minimum": 0,
            "maximum": 40
          }
        ]
      }
    ]
  }
}
```

The file must be valid UTF-8 XML, use `<status>` as its root, and not exceed
1 MB. DTDs and external entities are rejected. Missing, duplicated, and invalid
values are reported in the profile diagnostics.

## XML field mapping

`xml_mapping.json` connects firmware elements to stable application keys.
Example field:

```json
{
  "key": "GainSet",
  "id": "5.1.1.2",
  "name": "GainSet",
  "label": "Gain setpoint",
  "type": "number",
  "unit": "dB",
  "role": "gain_set",
  "group": "Amplifier",
  "writable": true,
  "minimum": 0,
  "maximum": 40,
  "alarm": {
    "enabled": true,
    "minimum": 10,
    "maximum": 35
  }
}
```

Property meanings:

- `key` — stable key used by the API and history,
- `id` or `name` — XML parameter selector,
- `label`, `unit`, `group` — description presented to the user,
- `type` — `number` or `text`,
- `role` — optional semantic role,
- `writable` — explicit permission to write the field to `control.xml`,
- `minimum`, `maximum` — optional limits for a value being written,
- `alarm.enabled` — enables threshold monitoring for the value being read,
- `alarm.minimum`, `alarm.maximum` — independent alarm limits.

The `alarm` block is optional. Empty, disabled alarm configuration is removed;
configured thresholds are retained when an alarm is disabled.

Only fields with `"writable": true` appear in **Control**. For numeric fields,
`minimum` and `maximum` define the accepted range; either may be omitted. With
no limits, the panel accepts any finite number and displays **No value range
configured**.

```json
"writable": true,
"minimum": 0,
"maximum": 40
```

Set these values in **Administration → Edit Variables**. They limit commands
written to `control.xml`; `alarm.minimum` and `alarm.maximum` define telemetry
warning thresholds.

The mapping can be edited in **Administration → Edit Variables**. It is loaded
on every XML read, so a valid change does not require a restart.

## Alarms and SNMP traps

The **Warnings** tab configures lower and upper telemetry limits for numeric
fields. Alarm settings are stored in `xml_mapping.json`.

An alarm opens only when a value crosses a configured boundary and clears when
the value returns to its valid range. `OPEN` and `CLEARED` events are written to
Syslog. On `OPEN`, the panel sends one SNMP trap to the configured destination.
Repeated readings of the same invalid value do not generate additional traps.

An alarm disappears after it has been acknowledged and the value has returned
to range. Acknowledging an active alarm does not hide it.

The **Send test trap** button in **SNMP Configuration** sends a test trap without
requiring a real alarm.

## Control: control.xml

A control request is validated against the mapping, assigned a UUID, and then
written atomically. Example:

```xml
<?xml version="1.0" encoding="utf-8"?>
<control version="1">
  <request id="11111111-1111-4111-8111-111111111111"
           created_at="2026-09-25T14:30:00+00:00">
    <device id="oba3">
      <parameter section="oba3" key="GainSet" type="number"
                 id="5.1.1.2" name="GainSet">
        <value>28.5</value>
      </parameter>
    </device>
  </request>
</control>
```

The device process must:

1. Read `control.xml` and validate `request.id`.
2. Apply each UUID at most once.
3. Publish the resulting values and acknowledgement in `status.xml`.

Acknowledgement in `status.xml`:

```xml
<control_status>
  <last_request_id>11111111-1111-4111-8111-111111111111</last_request_id>
  <state>applied</state>
  <message>OK</message>
</control_status>
```

Allowed device response states:

- `pending` — the device accepted the request,
- `applied` — the change was applied,
- `rejected` — the device rejected the value,
- `failed` — execution failed.

If a matching acknowledgement does not appear before
`XML_CONTROL_ACK_TIMEOUT_SECONDS`, the panel reports `timeout`.

## XML control API

Writing is available to Administrators and Operators:

```http
PUT /api/devices/oba3/control
Content-Type: application/json

{
  "values": {
    "oba3:GainSet": 28.5
  }
}
```

The response contains the `request_id`, the `pending` state, and the control file
path. Values are identified as `section:key`.

The **Control** tab reads the 15 latest requests from SQLite. Each entry contains
the values, state, message, UUID, time, and user. The API endpoint is:

```http
GET /api/devices/oba3/control/status
```

Syslog contains every requested value and observed state transition. Read the
complete control audit, including rotated logs, with:

```bash
sudo zgrep -h 'action=device_control_' /var/log/amp-panel/amp-panel.log*
```

The path follows `SYSLOG_EXPORT_FILE` and is shown in **Control**. The API rejects
read-only fields, invalid values, and values outside the configured range.

## Historical data in SQLite

`DATABASE_FILE` stores timestamped measurements separately for each profile. A
snapshot is added only when a value changes.

Stored history is used by:

- charts in **Overview**,
- calculations in **Statistics**,
- CSV data exports.

Hourly summaries speed up long-range queries. **Service Diagnostics** sets the
per-profile record limit; `0` keeps unlimited history.

The database file is managed automatically by the application and should not be
edited while the service is running.

## Diagnostics and service management

```bash
sudo amp-panel status
sudo amp-panel doctor
sudo amp-panel logs -n 100
sudo amp-panel logs -f
sudo amp-panel restart
sudo amp-panel paths
```

`amp-panel doctor` checks the configuration, data directory, SQLite database,
`status.xml`, `control.xml` write access, and systemd services.

Common problems:

- **No data** — check that `status.xml` exists and inspect its modification time.
- **Stale source** — the device process did not refresh the file before
  `XML_STALE_SECONDS` elapsed.
- **Control timeout** — the device did not return a matching UUID in
  `<control_status>`.
- **Read-only field** — the mapping does not contain `"writable": true`.
- **Permission denied** — the device process and the `amp-panel` user do not have
  the required access to the exchange directory.

## Development and testing

Install the Python dependencies:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

Run the tests:

```bash
python -m unittest discover -s tests -q
```

Check the Python code:

```bash
python -m ruff check .
python -m ruff format --check .
```

The frontend uses a locally bundled copy of Chart.js, so charts do not require
Internet access.
