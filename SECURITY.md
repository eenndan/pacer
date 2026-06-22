# Security Policy

Pacer is a local desktop application that processes telemetry and video files you provide.
It does not run a server or handle credentials, so its attack surface is small — but parsing
untrusted media files (GPMF/MP4) and shelling out to `ffmpeg` are the areas worth scrutiny.

## Reporting a vulnerability

Please **do not** open a public issue for a security problem. Instead, report it privately via
GitHub's [private vulnerability reporting](https://github.com/eenndan/pacer/security/advisories/new)
("Report a vulnerability" under the repository's **Security** tab).

Include:

- a description of the issue and its impact,
- steps to reproduce (a minimal input file is ideal),
- the affected version / commit.

You can expect an acknowledgement within a few days. Since this is a single-maintainer
project, please allow reasonable time for a fix before any public disclosure.

## Supported versions

This is pre-1.0 software; only the latest `main` is supported. Security fixes land on `main`.
