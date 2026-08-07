# Optical equipment control panel

The optical equipment control panel is a local web dashboard for monitoring and controlling one of two
device profiles:

- `amplifier` — an optical amplifier with line-oriented telemetry;
- `fts-ls` — a Frequency Transfer System laser station controlled through an
  authenticated serial console.

## Building the Debian package

Install the build tools once on a clean Debian system:

```console
sudo apt update
sudo apt install build-essential debhelper git python3 python3-pip
```

When already logged in as `root`, omit `sudo` from these commands.

The `debhelper` package provides the required `debhelper-compat (= 13)` build
dependency. Build the package from the project root:

```console
./packaging/build_deb.sh
```

The script checks the Debian build dependencies before starting and prints the
required installation command when one is missing. The resulting `.deb` file is
written to the parent directory of the project.

## Documentation

The complete operating, administration, and maintenance manual is maintained in
LaTeX:

- source: [`docs/AMP_PANEL_MANUAL.tex`](docs/AMP_PANEL_MANUAL.tex);
- printable output: `docs/AMP_PANEL_MANUAL.pdf`.

Compile it twice so the table of contents and references are updated:

```console
cd docs
pdflatex -interaction=nonstopmode -halt-on-error AMP_PANEL_MANUAL.tex
pdflatex -interaction=nonstopmode -halt-on-error AMP_PANEL_MANUAL.tex
```

The program-operation diagram is generated from code rather than captured from
the interface:

```console
python tools/generate_program_flow_diagram.py
```
## Setting up a RADIUS server

`amp-panel` does not host RADIUS itself. A reachable RADIUS server must
exist before you run the installer, on a separate host from the panel.

A ready-to-run installer for this server is included in the repository:

```bash
cd server_setup
sudo ./install_radius_server.sh
```

The script installs FreeRADIUS, prompts for the panel host's IP (or CIDR)
to register it as a RADIUS client, and generates a shared secret if you
don't provide one. Keep the printed secret — you'll need it during
`amp-panel configure`.

### Adding user accounts

Every username that will log into the panel — the initial administrator and
any Operator or Viewer accounts added later in Access Control — needs a
matching account on the RADIUS server. The panel only assigns a role and
active status to a username; it does not create or store RADIUS accounts
or passwords.

Accounts are managed directly on the RADIUS server, in
`/etc/freeradius/3.0/users`:

admin Cleartext-Password := "a-strong-password-here"


### If the panel is reachable through NAT or a container bridge

The address FreeRADIUS sees as the request source may not match the panel
host's own IP — for example when the panel runs in Docker, or reaches
RADIUS through a forwarded/NAT connection. If login fails with
"Authentication server unavailable" after installation, confirm the real
source address:

```bash
sudo systemctl stop freeradius
sudo freeradius -X
```

Attempt a login from the panel while this is running and look for a line
like `Ignoring request ... from unknown client X.X.X.X`. Add that address
(or a covering range) as a client in `/etc/freeradius/3.0/clients.conf`,
then restart normally:

```bash
sudo systemctl start freeradius
```

With the server running and at least one user account created, proceed to
`amp-panel configure` on the panel host and supply this server's address,
port (1812 by default), and the shared secret printed by the installer.
