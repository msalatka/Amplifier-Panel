# SNMP: pass_persist config export + threshold trap daemon

An exercise with net-snmp (`snmpd`) that adds two independent monitoring
mechanisms alongside the pysnmp-based agent already built into the app
(`app/services/snmp.py`):

1. **`amp_config_pass_persist.py`** — a `pass_persist` script for `snmpd`.
   Serves the live configuration set in the GUI (gain tolerance, warning
   thresholds `warn_limits`, SNMP community enabled state, last known gain
   set), reading directly from `persisted_state.json`. It does not poll
   anything in a loop — it only reacts to `snmpd` queries (`get`/`getnext`),
   per the pass_persist protocol.

2. **`amp_trap_daemon.py`** — a separate, continuously running process.
   Every 10s it reads the latest sample from `measurements.db` and the
   thresholds from `persisted_state.json`; on a threshold breach it sends
   an `SNMP TRAP` (`ACTIVE`), and once back within range, another TRAP
   (`CLEAR`). The trap is sent to the `trap_host`/`trap_port`/`community`
   configured in the GUI (SNMP Configuration). The trap itself is never
   stored locally — observing it requires an external receiver (e.g.
   Wireshark with filter `udp.port == 162`, or eventually Zabbix/snmptrapd).

## Installing on the device (Debian/BeagleBone)

```bash
sudo apt install -y snmpd snmp
sudo cp amp_config_pass_persist.py amp_trap_daemon.py /usr/local/bin/
sudo chmod +x /usr/local/bin/amp_config_pass_persist.py /usr/local/bin/amp_trap_daemon.py
```

Listen on all interfaces (by default `snmpd` only listens on `127.0.0.1`):

```bash
sudo sed -i 's/^agentaddress.*/agentaddress udp:161/' /etc/snmp/snmpd.conf
```

Community (the same one the app uses for its own agent):

```bash
COMMUNITY=$(sudo grep SNMP_COMMUNITY /etc/amp-panel/amp-panel.env | cut -d= -f2)
echo "rocommunity $COMMUNITY" | sudo tee -a /etc/snmp/snmpd.conf
echo "pass_persist .1.3.6.1.4.1.99999.10 /usr/local/bin/amp_config_pass_persist.py" | sudo tee -a /etc/snmp/snmpd.conf
```

### Permissions — an important detail

`persisted_state.json` and `measurements.db` are owned by the system user
`amp-panel` (`rw-------`/`drwxr-x---`). By default `snmpd` starts as root
but **drops privileges itself** to `Debian-snmp` (`-u Debian-snmp -g Debian-snmp`
in `ExecStart`), so adding it to the group is not enough — `ExecStart` must
be overridden so it does not drop privileges:

```bash
sudo mkdir -p /etc/systemd/system/snmpd.service.d
sudo tee /etc/systemd/system/snmpd.service.d/override.conf > /dev/null <<'EOF'
[Service]
ExecStart=
ExecStart=/usr/sbin/snmpd -LOw -I -smux,mteTrigger,mteTriggerConf -f
User=root
Group=root
EOF
sudo systemctl daemon-reload
sudo systemctl restart snmpd
```

### Running the trap daemon as a systemd service

```bash
sudo tee /etc/systemd/system/amp-trap-daemon.service > /dev/null <<'EOF'
[Unit]
Description=Amp Panel threshold trap daemon
After=network.target

[Service]
ExecStart=/usr/bin/python3 /usr/local/bin/amp_trap_daemon.py
Restart=on-failure
User=root

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now amp-trap-daemon
```

## Testing

From another machine on the same network (not from the device itself):

```bash
snmpwalk -v2c -c <community> <device_address> .1.3.6.1.4.1.99999.10
```

A threshold change made in the GUI should be visible in the next `snmpwalk`
immediately, with no restart of `snmpd` or the app.

SNMP traffic can be inspected on the device with:

```bash
sudo tcpdump -i any -n udp port 161 or udp port 162
```

## Known limitations of this exercise

- The OIDs (`.1.3.6.1.4.1.99999.10.*`) are private/test values, not
  registered with IANA — for demonstration use, not production.
- Running `snmpd` as `root` (without dropping privileges) simplifies access
  to the app's files but reduces process isolation — acceptable on a lab
  bench, worth revisiting before any production use (e.g. an ACL on the
  data file instead of changing the daemon's user).
