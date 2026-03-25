# parser.py
import dateparser
import pytz
from datetime import datetime

# Country -> timezone mapping
# For multi-timezone countries, maps to most common zone
COUNTRY_TIMEZONE_MAP = {
    # Asia
    "india": "Asia/Kolkata",
    "nepal": "Asia/Kathmandu",
    "pakistan": "Asia/Karachi",
    "bangladesh": "Asia/Dhaka",
    "sri lanka": "Asia/Colombo",
    "china": "Asia/Shanghai",
    "japan": "Asia/Tokyo",
    "south korea": "Asia/Seoul",
    "singapore": "Asia/Singapore",
    "malaysia": "Asia/Kuala_Lumpur",
    "thailand": "Asia/Bangkok",
    "vietnam": "Asia/Ho_Chi_Minh",
    "indonesia": "Asia/Jakarta",
    "philippines": "Asia/Manila",
    "uae": "Asia/Dubai",
    "saudi arabia": "Asia/Riyadh",
    "israel": "Asia/Jerusalem",
    "turkey": "Europe/Istanbul",

    # Europe
    "uk": "Europe/London",
    "united kingdom": "Europe/London",
    "france": "Europe/Paris",
    "germany": "Europe/Berlin",
    "italy": "Europe/Rome",
    "spain": "Europe/Madrid",
    "netherlands": "Europe/Amsterdam",
    "russia": "Europe/Moscow",
    "ukraine": "Europe/Kiev",
    "poland": "Europe/Warsaw",
    "sweden": "Europe/Stockholm",
    "norway": "Europe/Oslo",
    "denmark": "Europe/Copenhagen",
    "finland": "Europe/Helsinki",
    "switzerland": "Europe/Zurich",
    "portugal": "Europe/Lisbon",
    "greece": "Europe/Athens",

    # Americas
    "usa": "America/New_York",
    "united states": "America/New_York",
    "canada": "America/Toronto",
    "mexico": "America/Mexico_City",
    "brazil": "America/Sao_Paulo",
    "argentina": "America/Argentina/Buenos_Aires",
    "colombia": "America/Bogota",
    "chile": "America/Santiago",
    "peru": "America/Lima",

    # Africa
    "nigeria": "Africa/Lagos",
    "kenya": "Africa/Nairobi",
    "south africa": "Africa/Johannesburg",
    "egypt": "Africa/Cairo",
    "ghana": "Africa/Accra",

    # Oceania
    "australia": "Australia/Sydney",
    "new zealand": "Pacific/Auckland",
}

# Multi-timezone countries needing follow-up
MULTI_TIMEZONE_COUNTRIES = {
    "usa": {
        "eastern": "America/New_York",
        "central": "America/Chicago",
        "mountain": "America/Denver",
        "pacific": "America/Los_Angeles",
        "alaska": "America/Anchorage",
        "hawaii": "Pacific/Honolulu",
    },
    "united states": {
        "eastern": "America/New_York",
        "central": "America/Chicago",
        "mountain": "America/Denver",
        "pacific": "America/Los_Angeles",
        "alaska": "America/Anchorage",
        "hawaii": "Pacific/Honolulu",
    },
    "canada": {
        "eastern": "America/Toronto",
        "central": "America/Winnipeg",
        "mountain": "America/Edmonton",
        "pacific": "America/Vancouver",
        "atlantic": "America/Halifax",
    },
    "australia": {
        "eastern": "Australia/Sydney",
        "central": "Australia/Darwin",
        "western": "Australia/Perth",
    },
    "russia": {
        "moscow": "Europe/Moscow",
        "yekaterinburg": "Asia/Yekaterinburg",
        "novosibirsk": "Asia/Novosibirsk",
        "vladivostok": "Asia/Vladivostok",
    },
    "brazil": {
        "brasilia": "America/Sao_Paulo",
        "amazon": "America/Manaus",
        "acre": "America/Rio_Branco",
    },
}


def get_timezone_for_country(country: str):
    """
    Returns (timezone_str, needs_followup, followup_options)
    needs_followup is True for multi-timezone countries
    """
    country = country.strip().lower()

    if country in MULTI_TIMEZONE_COUNTRIES:
        options = MULTI_TIMEZONE_COUNTRIES[country]
        return None, True, options

    tz = COUNTRY_TIMEZONE_MAP.get(country)
    if tz:
        return tz, False, None

    # Try pytz directly in case user typed a valid tz string like "Asia/Kolkata"
    try:
        pytz.timezone(country)
        return country, False, None
    except pytz.UnknownTimeZoneError:
        return None, False, None


def parse_reminder_time(text: str, timezone_str: str):
    """
    Parses natural language or command-style time from text.
    Returns a timezone-aware datetime or None.
    Examples:
        "30m Call mom"        -> parses "30m" as 30 minutes from now
        "tomorrow 9am standup"
        "2h30m meeting"
    """
    tz = pytz.timezone(timezone_str)
    now = datetime.now(tz)

    settings = {
        'PREFER_DATES_FROM': 'future',
        'RETURN_AS_TIMEZONE_AWARE': True,
        'TIMEZONE': timezone_str,
        'RELATIVE_BASE': now,
    }

    parsed = dateparser.parse(text, settings=settings)
    return parsed


def split_time_and_message(text: str):
    """
    Splits user input into (time_part, message_part).
    Strategy: try parsing progressively longer prefixes until one works.
    E.g. "in 30 minutes call mom" -> ("in 30 minutes", "call mom")
         "tomorrow 9am doctor"    -> ("tomorrow 9am", "doctor")
         "30m take medicine"      -> ("30m", "take medicine")
    """
    words = text.split()

    for i in range(1, len(words)):
        time_candidate = " ".join(words[:i])
        message_candidate = " ".join(words[i:])

        if not message_candidate.strip():
            continue

        parsed = dateparser.parse(time_candidate, settings={
            'PREFER_DATES_FROM': 'future',
            'RETURN_AS_TIMEZONE_AWARE': True,
        })

        if parsed:
            return time_candidate, message_candidate

    return None, None
