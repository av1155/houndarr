# Houndarr

Houndarr is a self-hosted polite search scheduler for missing,
cutoff-unmet, and upgrade-eligible media across Sonarr, Radarr,
Lidarr, Readarr, and Whisparr instances. It triggers search commands
in small, rate-limited batches without managing downloads, parsing
releases, or evaluating quality.

## Language

### Credentials

**Instance API key** (also: *arr API key when contrasting):
The credential a *arr instance issues, used by Houndarr to
authenticate outbound calls to that *arr's REST API. Stored
Fernet-encrypted in `instances.encrypted_api_key`.
_Avoid_: "the API key" without qualifier when both inbound and
outbound credentials are in scope.

**Houndarr API key**:
The credential Houndarr issues so external tools (Homepage widgets,
custom integrations, CLI scrapers) can authenticate inbound calls to
Houndarr's external-stable API. Stored SHA-256-hashed in
`widget_api_key`; verified against the `X-Api-Key` header on
`/api/v1/widget`.
_Avoid_: "public API key" (the endpoint isn't unauthenticated),
"widget API key" (consumers aren't only widgets).

### Library state

**Tracked**:
Total items Houndarr's enabled, healthy instances are responsible
for. Equals **Eligible** + **Gated** + **Unreleased** + items in
upgrade cooldown.

**Eligible**:
Monitored items Houndarr can dispatch a search for right now (no
active cooldown, post-release grace expired).
_Avoid_: "wanted" (overloaded by *arr's `/wanted/*` API namespace),
"ready" (vague).

**Gated**:
Items in per-item cooldown after a recent missing or cutoff search.
Returns to **Eligible** once the cooldown elapses.
_Avoid_: "queued" (overloaded by *arr download-queue terminology).

**Unreleased**:
Monitored items still awaiting their release date. Becomes
**Eligible** automatically once released.

## Relationships

- An **Instance** owns one **Instance API key** (encrypted) and
  contributes per-instance counts to **Tracked**, **Eligible**,
  **Gated**, and **Unreleased** while enabled and healthy.
- A Houndarr installation owns at most one **Houndarr API key**
  (hashed) authorizing access to the external-stable API.
- The header `X-Api-Key` is used in both credential directions
  (Houndarr -> *arr outbound, external tool -> Houndarr inbound);
  the route disambiguates which key is expected.

## Example dialogue

> **Dev:** "Where do we store the API key?"
>
> **Maintainer:** "Which one? The **Instance API key** (the *arr's
> key Houndarr uses outbound) is Fernet-encrypted in
> `instances.encrypted_api_key`. The **Houndarr API key** (the key
> external tools use to call us inbound) is SHA-256-hashed in
> `widget_api_key`."

> **Dev:** "The library-health bar shows Eligible 1234, Gated 89,
> Unreleased 12. What's Tracked?"
>
> **Maintainer:** "Tracked is the rollup that includes those three
> plus upgrade cooldowns, which sit outside the *arr's `/wanted/*`
> namespace but are still items Houndarr is responsible for."

## Flagged ambiguities

- "API key" was used to mean both **Instance API key** (outbound,
  Fernet-encrypted) and **Houndarr API key** (inbound,
  SHA-256-hashed). Resolved: distinct concepts with distinct storage
  models. Use the qualified term whenever the contrast matters.
- "wanted" overlapped with *arr's `/wanted/*` API namespace and a
  vague user concept. Resolved: avoid "wanted" as a Houndarr-domain
  term; use **Eligible** / **Gated** / **Unreleased** for the
  precise states.
