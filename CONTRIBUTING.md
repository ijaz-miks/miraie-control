# Contributing

Bug reports, model compatibility reports and pull requests are welcome. Include
your operating system version, GNOME Shell version if applicable, AC model and firmware version if known,
and whether the problem affects the panel, standalone window or tray.

Describe the expected and actual behavior. Use synthetic examples when sharing
protocol payloads. Remove account identifiers, home and device IDs, MQTT topics,
MAC and IP addresses, passwords, bearer tokens, UDP keys and IVs. Screenshots can
contain room names and account details, so review them before sharing.

For code changes, follow the existing Python, JavaScript and Swift style, add regression
coverage for behavior changes, and run the checks in README.md. Protocol and GUI
tests use mock devices. Do not use real credentials in tests or CI.

Keep these behaviors intact:

- Device availability comes from explicit connection evidence.
- Cached MQTT status cannot promote a disconnected AC to online.
- Commands remain pending until fresh device status confirms the requested field.
- An uncertain write is not resent through another transport automatically.
- Model metadata determines advanced feature availability.
- Account secrets and local transport keys stay out of public snapshots and logs.

Local research and test output directories are ignored. Do not attach proprietary
APKs, decompiled application source or unredacted captures to an issue or PR.

For macOS changes, run `swift test --package-path macos` with full Xcode selected,
build a fresh app bundle with `scripts/build-macos.py`, and exercise the explicit
demo or UI smoke mode. Keep native system materials, accessibility labels,
offline guards and command confirmation intact. Never use a real account for
UI screenshots or automated tests.

Submit contributions under the project's MIT License. Please identify any
third-party code you introduce and preserve its original license notices.
