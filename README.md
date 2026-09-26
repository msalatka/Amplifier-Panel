# Amp Panel

Amp Panel is a local web application that reads device status and sends commands
to devices through XML files. It supports four profiles:

- `local` — local station / DI,
- `remote` — remote station / DI,
- `oba` — EDFA OBA amplifier,
- `oba3` — EDFA OBA3 amplifier.

The panel reads current device data from `status.xml` and writes commands and
settings to a separate `control.xml` file.

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

After changes are submitted, the bottom of the **Control** tab shows whether the
device has processed them. The result is read from `status.xml` and may indicate
that the command is waiting to be processed, has been applied, was rejected,
failed, or was not confirmed in time. Adding another control field does not
require a GUI change—add it to the mapping with its type, range, and `writable`
flag.

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

The main XML settings are:

```ini
ENABLED_DEVICES=local,remote,oba,oba3
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

After a configuration change, `amp-panel configure` validates the values,
prepares files and permissions, and restarts the services. The database, state,
and `control.xml` files must be located inside `AMP_PANEL_DATA_DIR`.

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

Sections used by the default profiles:

| Profile | XML sections |
|---|---|
| `local` | `params_local`, `params_localdi` |
| `remote` | `params_remote`, `params_remotedi` |
| `oba` | `params_oba` |
| `oba3` | `params_oba3` |

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

The `alarm` block is optional. If it is absent, the alarm is disabled. When the
**Warnings** tab is saved, the application does not add an empty
`{"enabled": false}` block. If the alarm is disabled and both thresholds are
empty, the existing block is removed from the mapping. A disabled alarm with a
configured minimum or maximum remains stored so that it can be enabled again
without losing its thresholds.

The panel never permits writing a field unless it has `"writable": true`. Set
the ranges according to the device specification; the application does not
guess safe values. Automatically discovered fields are read-only by default.

The mapping can be edited in **Administration → Edit Variables**. It is loaded
on every XML read, so a valid change does not require a restart.

## Alarms and SNMP traps

The **Warnings** tab displays active limit violations and allows Operators and
Administrators to configure alarms for all numeric fields. The configuration is
written directly to `xml_mapping.json`; there is no second threshold file. A
lower limit, upper limit, or both can be configured.

An alarm opens only when a value crosses a configured boundary and clears when
the value returns to its valid range. `OPEN` and `CLEARED` events are written to
Syslog. On `OPEN`, the panel sends one SNMP trap to the configured destination.
Repeated readings of the same invalid value do not generate additional traps.

An alarm remains visible until an Operator or Administrator acknowledges it.
Acknowledging an active alarm does not hide it; the entry disappears only after
it has both been acknowledged and returned to its valid range. This ensures that
a short alarm that clears before the page is opened still requires deliberate
acknowledgement. Alarms cannot be ignored.

The **Send test trap** button in **SNMP Configuration** sends a test trap without
requiring a real alarm.

Field-level `minimum` and `maximum` limits apply to values sent through
`control.xml`. Limits inside `alarm` apply only to telemetry read from
`status.xml`; the two mechanisms are intentionally separate.

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

The device process should:

1. Watch `control.xml`.
2. Check `request.id`.
3. Apply each UUID no more than once.
4. Ignore a UUID that has already been processed.
5. Place the result in the next `status.xml`.

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

Latest request status:

```http
GET /api/devices/oba3/control/status
```

Every write is recorded in the audit log. The API rejects read-only fields,
non-finite values, values outside their configured range, and excessively long
text values.

## Historical data in SQLite

The panel stores measurement history in the SQLite database specified by
`DATABASE_FILE`. Data for each device is stored separately together with its
read time. A new entry is created only when at least one device value changes.
Regularly refreshing an unchanged `status.xml` does not create duplicate
entries.

Stored history is used by:

- charts in **Overview**,
- calculations in **Statistics**,
- CSV data exports.

For longer periods, the panel uses hourly summaries so that it does not need to
process every individual measurement each time. The maximum number of stored
entries for each device can be configured in **Service Diagnostics**. When the
limit is reached, the oldest entries are removed. A value of `0` means unlimited
history.

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
