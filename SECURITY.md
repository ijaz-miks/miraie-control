# Security and privacy

Account usernames and passwords are stored through libsecret in GNOME Keyring.
The macOS client uses a separate generic-password item in the user's Keychain,
restricted to this device. Its URLSession uses ephemeral storage and standard
HTTPS certificate verification. It does not store account data or responses in
the repository, UserDefaults or an HTTP disk cache. UserDefaults stores only
the selected device identifier.
Session tokens and device UDP keys are held in process memory. Credentials are
not stored in the checkout, settings schema or command-line arguments. HTTPS and
MQTT use certificate verification. There is no project-hosted account service.

The MirAIe public client identifier and API endpoints in source describe the
protocol. They are shared application configuration, not user credentials.

The session D-Bus interface exposes device names, room names, device identifiers
and status to applications running in the same user session. It also accepts AC
commands from those applications. It is a local desktop interface, not a sandbox
boundary. The interface excludes account passwords, bearer tokens and UDP keys.

Ubuntu local UDP support is experimental and uses the device's observed AES-CBC
protocol. It does not add an integrity mechanism to that protocol. Use it only
on a trusted Wi-Fi network. Initial authentication and device metadata retrieval
require the MirAIe cloud service.

To report a vulnerability, use this repository's **Security > Report a
vulnerability** option. Include a minimal synthetic reproduction and affected
versions. Do not post live credentials or exploitable account details in a public
issue. If private reporting is unavailable, open a public issue asking for a
private contact method without disclosing the vulnerability details.
