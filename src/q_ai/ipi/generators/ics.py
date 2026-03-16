"""ICS (iCalendar) payload generator.

This module implements ICS-based hiding techniques targeting systems
that process calendar invites, including email clients, scheduling tools,
and AI assistants that summarize meeting invitations.

Techniques:
    ICS_DESCRIPTION: Payload in event DESCRIPTION property.
    ICS_LOCATION: Payload in event LOCATION property.
    ICS_VALARM: Payload in VALARM reminder DESCRIPTION.
    ICS_X_PROPERTY: Payload in custom X- extension property.

Targets:
    Email clients (Outlook, Gmail, Apple Mail), calendar applications,
    meeting summarizers, AI assistants processing calendar invites,
    scheduling automation tools.

Usage:
    >>> from q_ai.ipi.generators.ics import create_ics
    >>> from q_ai.ipi.models import Technique
    >>> campaign = create_ics(
    ...     Path("./meeting.ics"),
    ...     Technique.ICS_DESCRIPTION,
    ...     "http://localhost:8080"
    ... )
"""

import uuid
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from icalendar import Alarm, Calendar, Event

from q_ai.ipi.models import Campaign, Format, PayloadStyle, PayloadType, Technique

from . import create_campaign_ids, generate_payload

# ICS techniques
ICS_TECHNIQUES = [
    Technique.ICS_DESCRIPTION,
    Technique.ICS_LOCATION,
    Technique.ICS_VALARM,
    Technique.ICS_X_PROPERTY,
]
"""All ICS techniques for calendar invite processing pipelines."""


# =============================================================================
# Decoy Content
# =============================================================================


def _create_decoy_calendar(event_uid: str | None = None) -> tuple[Calendar, Event]:
    """Create a plausible calendar invite with decoy content.

    Args:
        event_uid: Optional deterministic UID for the event. When None,
            a random UUID is generated.

    Returns:
        Tuple of (Calendar, Event) objects with realistic meeting content.
    """
    cal = Calendar()
    cal.add("prodid", "-//Corporate Meeting System//meetings.example.com//")
    cal.add("version", "2.0")
    cal.add("method", "REQUEST")

    event = Event()

    # Realistic meeting details
    event.add("summary", "Q1 Strategy Review - All Hands")

    # Set meeting time (next Monday at 2pm UTC)
    now = datetime.now(ZoneInfo("UTC"))
    days_until_monday = (7 - now.weekday()) % 7
    if days_until_monday == 0:
        days_until_monday = 7
    meeting_start = now.replace(hour=14, minute=0, second=0, microsecond=0) + timedelta(
        days=days_until_monday
    )
    meeting_end = meeting_start + timedelta(hours=1)

    event.add("dtstart", meeting_start)
    event.add("dtend", meeting_end)
    event.add("dtstamp", now)

    # Generate unique ID
    uid = event_uid if event_uid else f"{uuid.uuid4()}"
    event.add("uid", f"{uid}@meetings.example.com")

    # Organizer and attendees
    event.add("organizer", "mailto:sarah.chen@example.com")
    event.add("attendee", "mailto:team@example.com")

    # Default description (may be replaced by payload)
    event.add(
        "description",
        "Join us for our quarterly strategy review. We'll discuss:\n"
        "- Q4 performance metrics\n"
        "- Q1 objectives and key results\n"
        "- Resource allocation updates\n\n"
        "Please review the attached deck before the meeting.",
    )

    # Default location (may be replaced by payload)
    event.add("location", "Conference Room A / Zoom: https://zoom.us/j/123456789")

    # Priority and status
    event.add("priority", 5)
    event.add("status", "CONFIRMED")

    return cal, event


# =============================================================================
# Technique Implementations
# =============================================================================


def _inject_description(event: Event, payload: str) -> None:
    """Inject payload into event DESCRIPTION property.

    The DESCRIPTION field is commonly extracted by email clients
    and AI assistants summarizing meeting invites.

    Args:
        event: Event to modify.
        payload: Payload string to inject.
    """
    # Replace description with payload appended to legitimate content
    original_desc = (
        "Join us for our quarterly strategy review. We'll discuss:\n"
        "- Q4 performance metrics\n"
        "- Q1 objectives and key results\n"
        "- Resource allocation updates\n\n"
        "Please review the attached deck before the meeting.\n\n"
        "---\n"
    )
    # Remove existing description and add new one with payload
    if "description" in event:
        del event["description"]
    event.add("description", original_desc + payload)


def _inject_location(event: Event, payload: str) -> None:
    """Inject payload into event LOCATION property.

    The LOCATION field is extracted for display and often
    processed by assistants looking for meeting details.

    Args:
        event: Event to modify.
        payload: Payload string to inject.
    """
    # Replace location with payload appended
    location_with_payload = (
        f"Conference Room A / Zoom: https://zoom.us/j/123456789\nNotes: {payload}"
    )
    if "location" in event:
        del event["location"]
    event.add("location", location_with_payload)


def _inject_valarm(event: Event, payload: str) -> None:
    """Inject payload into VALARM component DESCRIPTION.

    VALARM components define reminders. The DESCRIPTION field
    within alarms is often extracted by calendar processors
    and notification systems.

    Args:
        event: Event to modify.
        payload: Payload string to inject.
    """
    alarm = Alarm()
    alarm.add("action", "DISPLAY")
    alarm.add("description", f"Meeting Reminder: {payload}")
    alarm.add("trigger", timedelta(hours=-1))  # 1 hour before
    event.add_component(alarm)


def _inject_x_property(event: Event, payload: str) -> None:
    """Inject payload into custom X- extension property.

    X-properties are vendor extensions that may be extracted
    by calendar processing tools. Multiple X-properties are
    added to increase extraction likelihood.

    Args:
        event: Event to modify.
        payload: Payload string to inject.
    """
    # Add multiple X-properties to increase extraction chances
    event.add("x-meeting-notes", payload)
    event.add("x-additional-info", payload)
    event.add("x-custom-data", payload)


# =============================================================================
# Main ICS Creation
# =============================================================================


def create_ics(
    output_path: Path,
    technique: Technique,
    callback_url: str,
    payload_style: PayloadStyle = PayloadStyle.OBVIOUS,
    payload_type: PayloadType = PayloadType.CALLBACK,
    seed: int | None = None,
    sequence: int = 0,
) -> Campaign:
    """Generate an ICS file with hidden prompt injection payload.

    Creates a plausible calendar invite and injects the payload using
    the specified technique.

    Args:
        output_path: Where to save the ICS file.
        technique: Hiding technique (see ICS_TECHNIQUES).
        callback_url: Base URL for callbacks.
        payload_style: Style of payload content (obvious vs subtle).
        payload_type: Objective of the payload.

        seed: Optional seed for deterministic UUID/token generation.
        sequence: Sequence number for batch deterministic generation.

    Returns:
        Campaign object with UUID and metadata.

    Raises:
        ValueError: If technique is not an ICS technique.

    Example:
        >>> from q_ai.ipi.generators.ics import create_ics
        >>> from q_ai.ipi.models import Technique
        >>> campaign = create_ics(
        ...     Path("./meeting.ics"),
        ...     Technique.ICS_DESCRIPTION,
        ...     "http://localhost:8080"
        ... )
    """
    if technique not in ICS_TECHNIQUES:
        raise ValueError(f"Unsupported ICS technique: {technique.value}")

    canary_uuid, token = create_campaign_ids(seed, sequence)
    payload = generate_payload(callback_url, canary_uuid, payload_style, payload_type, token=token)

    # Create calendar with decoy content
    cal, event = _create_decoy_calendar()

    # Inject payload using selected technique
    if technique == Technique.ICS_DESCRIPTION:
        _inject_description(event, payload)
    elif technique == Technique.ICS_LOCATION:
        _inject_location(event, payload)
    elif technique == Technique.ICS_VALARM:
        _inject_valarm(event, payload)
    elif technique == Technique.ICS_X_PROPERTY:
        _inject_x_property(event, payload)

    # Add event to calendar
    cal.add_component(event)

    # Save calendar file
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as f:
        f.write(cal.to_ical())

    return Campaign(
        id=uuid.uuid4().hex,
        uuid=canary_uuid,
        token=token,
        filename=output_path.name,
        format=Format.ICS,
        technique=technique,
        payload_style=payload_style,
        payload_type=payload_type,
        callback_url=callback_url,
    )


# =============================================================================
# Batch Generation
# =============================================================================


def create_all_ics_variants(
    output_dir: Path,
    callback_url: str,
    base_name: str = "meeting",
    payload_style: PayloadStyle = PayloadStyle.OBVIOUS,
    payload_type: PayloadType = PayloadType.CALLBACK,
    techniques: list[Technique] | None = None,
    seed: int | None = None,
) -> list[Campaign]:
    """Generate ICS files using multiple techniques.

    Args:
        output_dir: Directory to save files.
        callback_url: Base URL for callbacks.
        base_name: Base filename (technique suffix will be added).
        payload_style: Style of payload content.
        payload_type: Objective of the payload.
        techniques: List of techniques to use (default: all ICS techniques).

        seed: Optional seed for deterministic UUID/token generation.

    Returns:
        List of Campaign objects.

    Example:
        >>> from q_ai.ipi.generators.ics import create_all_ics_variants
        >>> campaigns = create_all_ics_variants(
        ...     Path("./output"),
        ...     "http://localhost:8080"
        ... )
        >>> len(campaigns)
        4
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    campaigns = []

    if techniques is None:
        techniques = ICS_TECHNIQUES

    for i, technique in enumerate(techniques):
        filename = f"{base_name}_{technique.value}.ics"
        output_path = output_dir / filename
        campaign = create_ics(
            output_path,
            technique,
            callback_url,
            payload_style,
            payload_type,
            seed=seed,
            sequence=i,
        )
        campaigns.append(campaign)

    return campaigns
