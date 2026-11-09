# Security policy

## Supported versions

| Version | Supported |
|---|---|
| 1.x | yes |

## Reporting

Open a private security advisory or email the maintainers. Do not file a public issue for a
vulnerability that exposes a server, a credential or a signing key.

## Design constraints

Dittobuffer is a **local** server:

- The game listener binds `127.0.0.1` and terminates TLS.
- The admin panel is loopback-only and intentionally unauthenticated.
- Family credentials are stored as salted digests; session tokens are random 32-byte values.
- No telemetry and no outbound traffic, except the optional PokeAPI sprite lookup in the admin UI.

Threats that are explicitly out of scope: anyone with local access to the machine, anyone who can
run ADB on the device, and any deployment that binds the server to a public interface.

## Secrets

`src/dittobuffer/patcher/keys/dittobuffer-patch.jks` is a **development** signing key so that patched
APKs install over each other. Never use it for a distributed application. `config/tls/` contains a
self-signed test CA; regenerate it for your own host.
