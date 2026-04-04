---
sidebar_position: 4
title: Test Coverage
description: What Houndarr's test suite covers and how quality is enforced on every change.
---

# Test Coverage

Houndarr has a comprehensive test suite that runs automatically on every change before it can ship. No code merges without all tests passing.

## What is covered

**Search engine.** Missing, cutoff, and upgrade passes are tested end to end, including how they share a cycle without starving each other's caps, and how the engine handles bad or incomplete API responses from your *arr instances.

**Scheduling rules.** Cooldown windows, hourly caps, batch sizes, post-release grace periods, and the upgrade hard caps are all exercised at their boundary conditions.

**Supervisor.** Graceful shutdown, connection loss and recovery, staggered startup, and idempotent task management. Connection errors produce exactly one log entry per failure sequence; recovery produces exactly one.

**Clients.** All six client types (Sonarr, Radarr, Lidarr, Readarr, Whisparr v2, Whisparr v3) are tested for correct API paths, request payloads, queue status checks, and error propagation.

**Database.** Log purge, settings, and cooldown tracking are verified at their boundaries, including concurrent access.

**Routes and auth.** Every mutating endpoint is tested for CSRF enforcement, authentication guards, and correct response codes across all outcomes.

**Security.** A dedicated set of integration tests verifies immunity to every finding from the [Huntarr security review](../security/trust-and-security.md#huntarr-vulnerability-audit). A live smoke test also runs against a real Docker container in CI on every PR.
