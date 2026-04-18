---
sidebar_position: 5
title: Compatibility
description: Tested *arr versions and Readarr-fork compatibility for Houndarr.
---

# Compatibility

Houndarr talks to your *arr instances through their REST APIs (v3
for Radarr, Sonarr, and Whisparr; v1 for Lidarr and Readarr). The
table below lists the versions currently tested against.

## Tested versions

| Application | API version | Tested with |
|-------------|-------------|-------------|
| Radarr | v3 | 6.0.4.10291 |
| Sonarr | v3 | 4.0.17.2952 |
| Lidarr | v1 | 3.1.0.4875 |
| Readarr | v1 | 0.4.20.129 |
| Whisparr v2 | v3 | 2.2.0.108 |
| Whisparr v3 | v3 | 3.3.2.604 |

Whisparr v2 and v3 are separate applications with different APIs.
v2 is Sonarr-based (studio / episode model, Docker:
`hotio/whisparr:latest`). v3 is Radarr-based (scene / movie model,
Docker: `hotio/whisparr:v3`). Select the matching instance type in
Houndarr.

Any version that exposes the same API (v3 or v1 depending on the
app) should work. When you test a connection, Houndarr reads the
`appName` and `version` from the instance's system/status endpoint
and verifies it matches the type you selected. For Whisparr, it
also detects v2 / v3 version mismatches.

## Readarr forks

The original Readarr project was discontinued in June 2025.
Community forks that use the same v1 API are expected to work when
configured as type **Readarr**:

- [Bookshelf](https://github.com/pennydreadful/bookshelf)
- [Reading Glasses](https://github.com/blampe/rreading-glasses)
- [Faustvii's Readarr](https://github.com/Faustvii/Readarr)

Forks that return an unrecognized `appName` in their system / status
response still connect; the type check only rejects known
mismatches (for example pointing a "Radarr" config at a Sonarr
URL).
