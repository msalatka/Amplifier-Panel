# Optical equipment control panel

Local web application for monitoring and controlling one connected optical
device. The selected device profile determines the serial protocol and
available interface:

- `amplifier` — optical amplifier with line-oriented telemetry;
- `fts-ls` — Frequency Transfer System laser station with an authenticated serial console.

## Building the Debian package

Install the build dependencies once on a clean Debian system:

```console
sudo apt update
sudo apt install build-essential debhelper git python3 python3-pip
```

When already logged in as `root`, omit `sudo`. The `debhelper` package provides
the required `debhelper-compat (= 13)` dependency.

Build the package from the project root:

```console
./packaging/build_deb.sh
```

The script verifies the build dependencies and writes the resulting `.deb` file
to the parent directory of the project.

## Installing and configuring

Install the generated package, using its exact filename:

```console
cd ..
sudo apt install "./amp-panel_0.1.0_$(dpkg --print-architecture).deb"
```

The installer asks for the device profile, serial connection, initial
administrator, and authentication method. Choose either local passwords stored
on the panel host as salted hashes, or a RADIUS server. Configuration can be
repeated later:

```console
sudo amp-panel configure
```

`amp-panel configure` opens the complete configuration file in `$VISUAL`,
`$EDITOR`, or the system `editor`. When the editor closes, the values are
validated before the installed configuration is replaced and services are
reloaded. Use `sudo amp-panel configure --prompt` for the older question-and-
answer wizard.

Verify the installation with:

```console
sudo amp-panel doctor
sudo amp-panel status
```

## Frontend dependency

Chart.js `4.5.1` is pinned in `package.json` and stored locally in
`static/vendor/chart.js`. Charts therefore do not require Internet access. When
upgrading the library, update both the bundled script and its license file.

## Authentication

The selected method applies to all browser logins:

- **Local passwords**: the panel stores its users, roles, active state, and
  salted PBKDF2 password hashes in its restricted state file on the panel host.
  Administrators create users and change passwords in **Access Control**.
- **RADIUS**: the panel stores only the username, role, and active state. The
  RADIUS server verifies passwords and remains the source of user accounts.

To switch methods, run `sudo amp-panel configure` and select `local` or
`radius`. Selecting local authentication asks for a new initial administrator
password. RADIUS settings are retained when local authentication is selected,
so switching back does not require entering them again.

## Setting up a RADIUS server

RADIUS mode requires a reachable RADIUS server on a separate host.

The repository includes a FreeRADIUS setup script:

```console
cd server_setup
sudo ./install_radius_server.sh
```

The script asks for the panel host's IP address or CIDR, registers it as a
RADIUS client, and generates a shared secret when one is not supplied. Keep that
secret for `amp-panel configure`.

### Adding user accounts

Every user allowed to sign in to the panel must also have an account on the
RADIUS server. The panel stores the username, role, and active status, but it
does not create or store RADIUS passwords.

On a standard FreeRADIUS installation, accounts are defined in
`/etc/freeradius/3.0/users`, for example:

```text
admin Cleartext-Password := "a-strong-password-here"
```

### Diagnosing an unknown RADIUS client

When traffic passes through NAT or another forwarded network path, FreeRADIUS
may see a different source address than the panel host's address. To identify
it:

```console
sudo systemctl stop freeradius
sudo freeradius -X
```

Attempt a login and look for `Ignoring request ... from unknown client
X.X.X.X`. Add the reported address or an appropriate network range to
`/etc/freeradius/3.0/clients.conf`, then restart the service:

```console
sudo systemctl start freeradius
```

Finally, run `amp-panel configure` on the panel host and provide the RADIUS
server address, UDP port (`1812` by default), and shared secret.
