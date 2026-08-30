# Security Policy

Camicia processes untrusted input from anyone on the internet: result
files uploaded by BOINC clients, and requests to the public web app
(registration, forums, preferences). If you find a security
vulnerability, please report it privately rather than opening a public
issue.

## Reporting a vulnerability

Email **admin@camicia.dev** with a description of the issue and, if
possible, steps to reproduce it. You can expect an initial response
within a few days.

Please do not open a public GitHub issue for security reports.

## Scope

In scope: the server-side code in this repository (PHP web app, C++
daemons/worker, deployment scripts/config).

Out of scope: the BOINC client itself and third-party BOINC server
code this project vendors unmodified (see individual files' headers).

## Further reading

This project follows upstream BOINC's own security guidance, not something invented here:

- [Code Signing](https://github.com/BOINC/boinc/wiki/Code-signing): key generation, offline
  storage, and rotation. `RUNBOOK.md`'s own key-rotation procedure follows this directly.
- [BOINC Security](https://github.com/BOINC/boinc/wiki/BOINC_Security): the wider threat model
  BOINC projects and volunteers operate under (code signing, sandboxing, spoofed projects, and more).
