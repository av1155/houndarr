"""Validation helpers, form-output dataclasses, and the live probe.

Owns the pure validation logic and the live connection probe that
:mod:`houndarr.services.instance_submit` composes into a single
orchestration.  Keeping the pure logic here means it can be tested
without the FastAPI machinery, and the HTTP-shaped helpers in
:mod:`houndarr.routes.settings._helpers` stay focused on request
plumbing (render, master_key lookup, connection-guard response
shaping).

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
sentinel-not-raise convention is intentional: the submit service
orchestrates multiple validators before rendering the guard banner,
and raising on the first failure would cascade into the template
flow the service owns.
"""

from __future__ import annotations

from dataclasses import dataclass

from houndarr.clients.base import ArrClient
from houndarr.clients.lidarr import LidarrClient
from houndarr.clients.radarr import RadarrClient
from houndarr.clients.readarr import ReadarrClient
from houndarr.clients.sonarr import SonarrClient
from houndarr.clients.whisparr_v2 import WhisparrV2Client
from houndarr.clients.whisparr_v3 import WhisparrV3Client
from houndarr.services.instances import (
    InstanceType,
    LidarrSearchMode,
    ReadarrSearchMode,
    SonarrSearchMode,
    WhisparrV2SearchMode,
    get_instance,
)
from houndarr.services.url_validation import validate_instance_url

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
    mismatch so operators can still adopt forks that self-report a
    novel name.

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

    Kept as a plain class with ``__slots__`` (rather than a
    ``@dataclass``) because the four per-app
    :class:`enum.StrEnum` fields are the only state the
    instance-submit path reads back; adding dataclass machinery
    buys nothing here.
    """

    __slots__ = ("lidarr", "readarr", "sonarr", "whisparr_v2")

    def __init__(
        self,
        sonarr: SonarrSearchMode,
        lidarr: LidarrSearchMode,
        readarr: ReadarrSearchMode,
        whisparr_v2: WhisparrV2SearchMode,
    ) -> None:
        self.sonarr = sonarr
        self.lidarr = lidarr
        self.readarr = readarr
        self.whisparr_v2 = whisparr_v2


def resolve_search_modes(
    instance_type: InstanceType,
    sonarr_raw: str,
    lidarr_raw: str,
    readarr_raw: str,
    whisparr_v2_raw: str,
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
        sonarr_raw / lidarr_raw / readarr_raw / whisparr_v2_raw: Raw
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
        whisparr_v2_mode = (
            WhisparrV2SearchMode(whisparr_v2_raw)
            if instance_type == InstanceType.whisparr_v2
            else WhisparrV2SearchMode.episode
        )
    except ValueError:
        return "Invalid Whisparr v2 search mode."

    return SearchModes(
        sonarr=sonarr_mode,
        lidarr=lidarr_mode,
        readarr=readarr_mode,
        whisparr_v2=whisparr_v2_mode,
    )


_CLIENT_CONSTRUCTORS: dict[InstanceType, type[ArrClient]] = {
    InstanceType.radarr: RadarrClient,
    InstanceType.sonarr: SonarrClient,
    InstanceType.lidarr: LidarrClient,
    InstanceType.readarr: ReadarrClient,
    InstanceType.whisparr_v2: WhisparrV2Client,
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


@dataclass(frozen=True, slots=True)
class ConnectionTestOutcome:
    """Route-shaped result of the full test-connection orchestration.

    The three fields map 1:1 to what the settings route's status
    snippet renders: a reachable-or-not flag, the user-facing message,
    and the HTTP status code.  Wrapping them in a frozen dataclass
    lets the route stay a pure dispatch (one call, one render) without
    branching inside the handler body.
    """

    ok: bool
    message: str
    status_code: int


async def run_connection_test(
    *,
    master_key: bytes,
    type_value: str,
    url: str,
    api_key: str,
    instance_id: str = "",
) -> ConnectionTestOutcome:
    """Orchestrate the full test-connection flow, including the sentinel path.

    Wraps the four concerns the ``POST /settings/instances/test-connection``
    route used to carry inline: parse the form's type string into
    :class:`InstanceType`; SSRF-gate the URL via
    :func:`houndarr.services.url_validation.validate_instance_url`;
    resolve the edit-form ``__UNCHANGED__`` sentinel against the
    stored api_key (look up by ``instance_id`` and pull the already-
    decrypted value); call :func:`check_connection`; check type
    mismatch.

    Args:
        master_key: Fernet key used to decrypt the stored api_key
            when the sentinel path applies.
        type_value: Raw instance-type string from the form
            (``"sonarr"``, ``"radarr"``, ``"lidarr"``, ``"readarr"``,
            ``"whisparr_v2"``, ``"whisparr_v3"``).
        url: Raw URL string from the form.  Trailing slashes are
            stripped before the live probe so the client's base URL
            never double-joins.
        api_key: Raw api_key string from the form.  The
            :data:`API_KEY_UNCHANGED` sentinel triggers the stored-
            key lookup; any other value is used verbatim.
        instance_id: Optional raw instance_id string from the form.
            Required only when ``api_key`` is the sentinel; empty or
            non-numeric means the sentinel resolves to a 422.

    Returns:
        :class:`ConnectionTestOutcome` with the three fields the route
        plugs straight into :func:`connection_status_response`.
    """
    try:
        instance_type = InstanceType(type_value)
    except ValueError:
        return ConnectionTestOutcome(ok=False, message="Invalid instance type.", status_code=422)

    url_error = validate_instance_url(url)
    if url_error is not None:
        return ConnectionTestOutcome(ok=False, message=url_error, status_code=422)

    resolved_api_key = api_key
    if api_key == API_KEY_UNCHANGED:
        # A sentinel submission requires an instance_id so the service
        # can pull the stored plaintext key.  The add-instance form
        # never sends the sentinel; the edit form always populates
        # instance_id.  A hand-crafted POST that sends the sentinel
        # with no instance_id used to fall through and probe the
        # remote with the literal string, which the *arr rejected as
        # an invalid key and the user saw a generic "Connection
        # failed" message.  Return a specific 422 instead.
        if not instance_id:
            return ConnectionTestOutcome(
                ok=False,
                message="Provide an API key.",
                status_code=422,
            )
        try:
            iid = int(instance_id)
        except ValueError:
            return ConnectionTestOutcome(
                ok=False,
                message="Invalid instance ID for key lookup.",
                status_code=422,
            )
        existing = await get_instance(iid, master_key=master_key)
        if existing is None:
            return ConnectionTestOutcome(ok=False, message="Instance not found.", status_code=404)
        resolved_api_key = existing.core.api_key

    check = await check_connection(instance_type, url.rstrip("/"), resolved_api_key)
    if not check.reachable:
        return ConnectionTestOutcome(
            ok=False,
            message="Connection failed. Check URL/API key and try again.",
            status_code=422,
        )

    mismatch = type_mismatch_message(check, instance_type)
    if mismatch is not None:
        return ConnectionTestOutcome(ok=False, message=mismatch, status_code=422)

    # "save changes" is shown when editing an existing instance
    # (instance_id set); "add this instance" is shown when the form
    # is creating a new one.
    action = "save changes" if instance_id else "add this instance"
    if check.app_name and check.version:
        message = f"Connected to {check.app_name} v{check.version}. You can now {action}."
    elif check.app_name:
        message = f"Connected to {check.app_name}. You can now {action}."
    else:
        message = f"Connection successful. You can now {action}."
    return ConnectionTestOutcome(ok=True, message=message, status_code=200)


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
