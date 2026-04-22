"""Validation helpers, form-output dataclasses, and the live probe.

Track D.11 lifted the static validators and their data structures
out of :mod:`houndarr.routes.settings._helpers` into this service
module so the HTTP-shaped helpers in ``_helpers.py`` could stay
focused on request plumbing (render, master_key lookup,
connection-guard response shaping) and the pure logic became
testable without the FastAPI machinery.  A follow-up to D.11 also
moves the ``API_KEY_UNCHANGED`` form sentinel, the
:func:`build_client` client factory, and the :func:`check_connection`
live probe here so :mod:`houndarr.services.instance_submit` depends
only on the service layer; previously it had to reach back into
``_helpers.py`` to pick those up, inverting the expected
service -> route dependency direction.

Contents:

- :class:`ConnectionCheck` captures the result of the live
  connection probe (reachable flag + app name + version).
- :class:`SearchModes` captures the four resolved per-app enum
  values :func:`resolve_search_modes` returns.
- :data:`API_KEY_UNCHANGED` is the form-layer sentinel the
  edit-instance partial submits when the operator has not changed
  the stored key; the service substitutes the existing plaintext
  key when it sees the sentinel.
- :func:`build_client` routes a selected :class:`InstanceType` to
  its concrete client class.
- :func:`check_connection` opens a client, calls ``ping()``, and
  packages the result as a :class:`ConnectionCheck`.  This is the
  one non-pure function in the module: it makes a live HTTP request
  through the client layer.  Kept here rather than in a
  dedicated one-function module because it builds and consumes
  :class:`ConnectionCheck` directly, and splitting it out would
  force every caller to import from two neighbouring service
  modules instead of one.

Validators return ``str | None`` where the string is the user-facing
error message.  :mod:`houndarr.services.instance_submit` converts
non-``None`` returns into :class:`~houndarr.errors.InstanceValidationError`
so the route layer never sees the bare string contract.  The
sentinel pattern survived the move intentionally: both D.10 (submit
orchestration) and the pre-refactor route handlers lean on it, and
raising early would have cascaded into the route's guard-banner
logic that the service now owns.
"""

from __future__ import annotations

from dataclasses import dataclass

from houndarr.clients.base import ArrClient
from houndarr.clients.lidarr import LidarrClient
from houndarr.clients.radarr import RadarrClient
from houndarr.clients.readarr import ReadarrClient
from houndarr.clients.sonarr import SonarrClient
from houndarr.clients.whisparr_v2 import WhisparrClient
from houndarr.clients.whisparr_v3 import WhisparrV3Client
from houndarr.services.instances import (
    InstanceType,
    LidarrSearchMode,
    ReadarrSearchMode,
    SonarrSearchMode,
    WhisparrSearchMode,
)

API_KEY_UNCHANGED = "__UNCHANGED__"
"""Sentinel sent back in the edit form to indicate the stored key is kept."""


@dataclass(frozen=True, slots=True)
class ConnectionCheck:
    """Result of a connection test against an *arr instance.

    ``reachable`` is the only field that is always populated; the
    other two carry the remote's self-reported name + version when
    the probe succeeded and stay ``None`` when it failed.
    """

    reachable: bool
    app_name: str | None = None
    version: str | None = None


_APP_NAME_TO_TYPE: dict[str, InstanceType] = {
    "radarr": InstanceType.radarr,
    "sonarr": InstanceType.sonarr,
    "lidarr": InstanceType.lidarr,
    "readarr": InstanceType.readarr,
    # Whisparr v2 and v3 both report appName "Whisparr"; version-based
    # disambiguation is handled in type_mismatch_message.
    "whisparr": InstanceType.whisparr_v2,
}


def _whisparr_version_major(version: str | None) -> int | None:
    """Extract the major version number from a Whisparr version string.

    Args:
        version: Remote-reported version string (e.g. ``"3.0.1.123"``),
            or ``None`` when the probe did not return one.

    Returns:
        The integer major version, or ``None`` when the input is
        missing or unparsable.
    """
    if not version:
        return None
    try:
        return int(version.split(".")[0])
    except (ValueError, IndexError):
        return None


def type_mismatch_message(check: ConnectionCheck, selected: InstanceType) -> str | None:
    """Return a human-readable mismatch message, or ``None`` when the type fits.

    The Whisparr family is the subtle case: v2 and v3 share the
    ``appName`` value ``"Whisparr"``, so the function disambiguates
    on the major-version number.  Any other app name is looked up
    against the lowercase map; an unknown app name (e.g. a Readarr
    fork that has renamed itself) is allowed through without a
    mismatch, matching the pre-refactor behaviour.

    Args:
        check: Result from a live :func:`check_connection` probe.
        selected: The :class:`InstanceType` the user picked in the
            form.

    Returns:
        The user-facing mismatch message, or ``None`` when the
        selected type is consistent with the remote's self-report.
    """
    if check.app_name is None:
        return None

    app_lower = check.app_name.lower()
    detected = _APP_NAME_TO_TYPE.get(app_lower)

    if app_lower == "whisparr":
        major = _whisparr_version_major(check.version)
        if major is not None and major >= 3 and selected == InstanceType.whisparr_v2:
            return (
                f"Version mismatch: this URL runs Whisparr v3 ({check.version})."
                " Select 'Whisparr v3' as the instance type."
            )
        if major is not None and major < 3 and selected == InstanceType.whisparr_v3:
            return (
                f"Version mismatch: this URL runs Whisparr v2 ({check.version})."
                " Select 'Whisparr v2' as the instance type."
            )
        return None

    if detected is None:
        return None
    if detected != selected:
        return f"Type mismatch: this URL is running {check.app_name}, not {selected.value.title()}."
    return None


def validate_cutoff_controls(
    cutoff_batch_size: int,
    cutoff_cooldown_days: int,
    cutoff_hourly_cap: int,
) -> str | None:
    """Validate cutoff-specific numeric controls from form submissions.

    Returns:
        User-facing error string on the first failed bound, or
        ``None`` when every value is valid.
    """
    if cutoff_batch_size < 1:
        return "Cutoff batch size must be at least 1."
    if cutoff_cooldown_days < 0:
        return "Cutoff cooldown days must be 0 or greater."
    if cutoff_hourly_cap < 0:
        return "Cutoff hourly cap must be 0 or greater."
    return None


def validate_upgrade_controls(
    upgrade_batch_size: int,
    upgrade_cooldown_days: int,
    upgrade_hourly_cap: int,
) -> str | None:
    """Validate upgrade-specific numeric controls from form submissions.

    The upgrade cooldown has a hard floor of 7 days (not 0) because
    upgrade searches target items that already have files on disk;
    a tighter cooldown would thrash the indexers for minimal
    benefit.  The route form enforces this with a ``min="7"``
    attribute; the service validator backs it up so a crafted POST
    cannot bypass the UI constraint.

    Returns:
        User-facing error string on the first failed bound, or
        ``None`` when every value is valid.
    """
    if upgrade_batch_size < 1:
        return "Upgrade batch size must be at least 1."
    if upgrade_cooldown_days < 7:
        return "Upgrade cooldown days must be at least 7."
    if upgrade_hourly_cap < 0:
        return "Upgrade hourly cap must be 0 or greater."
    return None


class SearchModes:
    """Resolved per-app search mode enum values.

    Kept as a class with ``__slots__`` (rather than a ``@dataclass``)
    to preserve the pre-refactor wire shape exactly; the four
    per-app :class:`enum.StrEnum` fields are the only state the
    instance-submit path reads back.
    """

    __slots__ = ("lidarr", "readarr", "sonarr", "whisparr")

    def __init__(
        self,
        sonarr: SonarrSearchMode,
        lidarr: LidarrSearchMode,
        readarr: ReadarrSearchMode,
        whisparr: WhisparrSearchMode,
    ) -> None:
        self.sonarr = sonarr
        self.lidarr = lidarr
        self.readarr = readarr
        self.whisparr = whisparr


def resolve_search_modes(
    instance_type: InstanceType,
    sonarr_raw: str,
    lidarr_raw: str,
    readarr_raw: str,
    whisparr_raw: str,
) -> SearchModes | str:
    """Validate and resolve per-app search mode strings into enum values.

    Non-applicable search modes default to their enum's first value
    so the resulting :class:`SearchModes` is always complete; the
    database write path needs a concrete value in every column even
    when the selected ``instance_type`` only consults one of them.

    Args:
        instance_type: The selected :class:`InstanceType`.  Drives
            which of the four raw strings actually gets parsed;
            the rest fall back to their enum default.
        sonarr_raw / lidarr_raw / readarr_raw / whisparr_raw: Raw
            form values.

    Returns:
        :class:`SearchModes` when every value resolves cleanly, or
        a user-facing error string on the first invalid mode.
    """
    try:
        sonarr_mode = (
            SonarrSearchMode(sonarr_raw)
            if instance_type == InstanceType.sonarr
            else SonarrSearchMode.episode
        )
    except ValueError:
        return "Invalid Sonarr search mode."

    try:
        lidarr_mode = (
            LidarrSearchMode(lidarr_raw)
            if instance_type == InstanceType.lidarr
            else LidarrSearchMode.album
        )
    except ValueError:
        return "Invalid Lidarr search mode."

    try:
        readarr_mode = (
            ReadarrSearchMode(readarr_raw)
            if instance_type == InstanceType.readarr
            else ReadarrSearchMode.book
        )
    except ValueError:
        return "Invalid Readarr search mode."

    try:
        whisparr_mode = (
            WhisparrSearchMode(whisparr_raw)
            if instance_type == InstanceType.whisparr_v2
            else WhisparrSearchMode.episode
        )
    except ValueError:
        return "Invalid Whisparr search mode."

    return SearchModes(
        sonarr=sonarr_mode,
        lidarr=lidarr_mode,
        readarr=readarr_mode,
        whisparr=whisparr_mode,
    )


_CLIENT_CONSTRUCTORS: dict[InstanceType, type[ArrClient]] = {
    InstanceType.radarr: RadarrClient,
    InstanceType.sonarr: SonarrClient,
    InstanceType.lidarr: LidarrClient,
    InstanceType.readarr: ReadarrClient,
    InstanceType.whisparr_v2: WhisparrClient,
    InstanceType.whisparr_v3: WhisparrV3Client,
}


def build_client(instance_type: InstanceType, url: str, api_key: str) -> ArrClient:
    """Construct the *arr client matching *instance_type*.

    Args:
        instance_type: The :class:`InstanceType` selected in the
            form.
        url: Base URL of the remote *arr.
        api_key: Plaintext API key for the remote.

    Returns:
        An unopened :class:`~houndarr.clients.base.ArrClient`; the
        caller enters it via ``async with``.

    Raises:
        ValueError: When *instance_type* has no registered client
            class.  The :class:`InstanceType` enum is the authority
            for valid values, so this only triggers on programmer
            error during future migrations.
    """
    client_cls = _CLIENT_CONSTRUCTORS.get(instance_type)
    if client_cls is None:
        msg = f"No client for instance type: {instance_type!r}"
        raise ValueError(msg)
    return client_cls(url=url, api_key=api_key)


async def check_connection(
    instance_type: InstanceType,
    url: str,
    api_key: str,
) -> ConnectionCheck:
    """Probe the remote *arr and return a :class:`ConnectionCheck`.

    Opens a client, calls ``ping()``, and converts the result into
    the service's :class:`ConnectionCheck` dataclass so every
    caller (the submit service and the route's explicit test
    connection button) speaks one shape.

    Args:
        instance_type: The :class:`InstanceType` selected in the
            form.  Drives which client class is instantiated.
        url: Base URL of the remote *arr.
        api_key: Plaintext API key for the remote.

    Returns:
        :class:`ConnectionCheck` with ``reachable=True`` plus the
        remote's self-reported ``app_name`` and ``version`` on
        success, or ``reachable=False`` with both optional fields
        ``None`` on any probe failure (transport, HTTP error, or
        client validation error; the client's ``ping()`` wraps all
        three into a single ``None`` return per
        :data:`~houndarr.clients.base.ArrClient._PING_SAFE_ERRORS`).
    """
    client = build_client(instance_type, url, api_key)
    async with client:
        status = await client.ping()
    if status is None:
        return ConnectionCheck(reachable=False)
    return ConnectionCheck(
        reachable=True,
        app_name=status.app_name,
        version=status.version,
    )
