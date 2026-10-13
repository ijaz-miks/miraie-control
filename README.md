# MirAIe Control

Control Panasonic MirAIe air conditioners from the Ubuntu desktop, with a GNOME
top-bar menu and a GTK/libadwaita control window. This is an independent community
project, licensed under MIT and not affiliated with Panasonic.

## Features

- Select between the air conditioners already registered in your MirAIe account.
- Control power, target temperature, mode, fan speed and swing.
- Show room temperature and model-supported display, nanoe and capacity controls.
- Keep pending changes separate from confirmed device settings.
- Show disconnected devices as offline, with cached temperatures labelled as
  last known. Retained MQTT messages cannot make an offline AC appear online.
- Store account credentials in GNOME Keyring.
- Use the native GNOME panel, standalone window or AppIndicator tray fallback.

Advanced controls depend on model metadata and firmware. This is an early
project with limited real-device validation.

## Compatibility and connectivity

The native extension has been tested on **Ubuntu 26.04 with GNOME Shell 50**.
Its metadata currently supports GNOME 50 only. Other distributions and GNOME
versions have not been validated. The tray requires an AppIndicator host, such
as Ubuntu's AppIndicator extension.

Your AC must already be paired with the official MirAIe app and connected to
Wi-Fi. Cloud control requires Internet access on both the AC and the desktop.
An Android phone, USB connection, REA or a decompiler is not needed to install
or use this project. Initial account setup supports email or a mobile number
with country code and password, using the MirAIe service at `miraie.in`.

**Local UDP control is experimental.** It is available only when the model
advertises `udpControl`, its firmware meets the declared minimum, and the cloud
returns valid device keys. Discovery uses the desktop's active Wi-Fi subnet and
UDP port 5001, so client isolation or an occupied port can prevent it from
working. Initial authentication and discovery still need Internet access.
Encryption and packet handling have mock tests; offline control has not been
validated on physical hardware. Do not assume every MirAIe AC supports it.

## Install on Ubuntu

Run these commands as your normal desktop user. Use the distribution Python
(`/usr/bin/python3`) so it can find the system PyGObject packages.

```sh
sudo apt update
sudo apt install git python3-gi python3-cryptography libglib2.0-bin \
  gir1.2-gtk-4.0 gir1.2-gtk-3.0 gir1.2-adw-1 gir1.2-secret-1 \
  gir1.2-ayatanaappindicator3-0.1 gnome-keyring

git clone https://github.com/ijaz-miks/miraie-control.git
cd miraie-control
/usr/bin/python3 scripts/vendor-paho.py
/usr/bin/python3 scripts/install-local.py --dry-run
/usr/bin/python3 scripts/install-local.py
```

The dependency script verifies the pinned Paho MQTT 2.1.0 wheel against a fixed
SHA256, retains its license files, and can be rerun to verify an existing copy.
It does not modify your global Python environment.

The installer places the backend under `~/.local/share/miraie-control`, the
extension under `~/.local/share/gnome-shell/extensions`, and launchers under
`~/.local/bin`. It registers a session D-Bus service, a desktop application and
a tray autostart entry, and adds this extension to the enabled-extension list.
Existing destinations are refused rather than overwritten. The installer does
not currently implement upgrades or automatic rollback. If a destination
already exists, preserve it or move it to Trash before a fresh installation.

Log out and back in so GNOME discovers the newly installed extension. Open
**MirAIe AC Controls** from the application launcher and sign in in that window.
Your keyring must be available and unlocked. The top-bar snowflake menu then
shows your devices and their confirmed state.

To open the standalone window or tray manually:

```sh
~/.local/bin/miraie-control-settings
~/.local/bin/miraie-control-tray
```

The tray exits when it detects that the native extension is active. Account
Settings in the panel opens the standalone window. Use **Refresh** after a
network interruption. A command timeout means the change was not confirmed;
refresh before deciding whether to retry.

## How it works

The desktop interfaces communicate with one user-session D-Bus backend. The
backend discovers homes, devices and capabilities over HTTPS, subscribes to
status updates over TLS MQTT, and sends commands through MQTT or HTTPS. It
verifies fresh device status before displaying a requested setting as confirmed.
An uncertain write is not automatically resent through a second transport.

| Component | Purpose |
| --- | --- |
| `backend/core.py` | Cloud protocol, state normalization and confirmed commands |
| `backend/local.py` | Experimental model-gated UDP discovery and encryption |
| `backend/service.py` | Session D-Bus service |
| `backend/credentials.py` | GNOME Keyring storage |
| `backend/settings.py` | GTK 4/libadwaita account and control window |
| `backend/tray.py` | GTK 3/Ayatana tray fallback |
| `extension/` | Native GNOME Shell menu |

The protocol was investigated using [REA](https://github.com/morluto/rea) and
JADX. The controller is independently implemented. Proprietary APKs, decompiled
source and private device captures are excluded from this repository. REA is
not a runtime dependency.

## Development and tests

After installing the dependencies above, install `gjs` for the JavaScript tests.
Run the protocol, installer and state regression checks:

```sh
/usr/bin/python3 -m unittest discover -s tests
gjs -m extension/tests/state.test.js
glib-compile-schemas --strict --dry-run extension/schemas
```

Within a graphical desktop session, run the mock GUI tests:

```sh
/usr/bin/python3 tests/gtk-smoke.py
/usr/bin/python3 tests/tray-smoke.py
```

On GNOME 50, the full extension can be tested in an isolated headless Shell:

```sh
/usr/bin/python3 scripts/shell-test.py
```

The tests use synthetic device responses, fake sockets and mock D-Bus controls.
They do not read account secrets or send commands to real air conditioners.
Installer and Shell test output is retained under the ignored `test-output/`
directory. CI runs the protocol, installer, JavaScript and GTK smoke checks.

Real-device validation has confirmed cloud discovery, online/offline handling,
and a target-temperature change followed by restoration. That does not establish
compatibility with every model or validate experimental local UDP control.

## Contributing, security and license

See [CONTRIBUTING.md](CONTRIBUTING.md) for compatibility reports and development
guidelines, [SECURITY.md](SECURITY.md) for credential handling and vulnerability
reporting, and [NOTICE](NOTICE) for third-party attribution. Please redact account
and device identifiers from issues, logs and screenshots.

Project source is covered by the [MIT License](LICENSE). Dependencies retain
their original upstream licenses.
