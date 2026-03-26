# parser.py
import re
from datetime import datetime, timedelta
import pytz


# Country -> timezone mapping
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

# Multi-timezone countries
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
    """
    country = country.strip().lower()

    if country in MULTI_TIMEZONE_COUNTRIES:
        options = MULTI_TIMEZONE_COUNTRIES[country]
        return None, True, options

    tz = COUNTRY_TIMEZONE_MAP.get(country)
    if tz:
        return tz, False, None

    # Try direct pytz timezone string
    try:
        pytz.timezone(country)
        return country, False, None
    except pytz.UnknownTimeZoneError:
        return None, False, None


def parse_time_string(time_str: str):
    """
    Parses time strings like 30s, 30m, 2h, 2h30m into total seconds.
    Returns total seconds or None if invalid.
    """
    time_str = time_str.strip().lower()
    pattern = re.compile(r'^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$')
    match = pattern.fullmatch(time_str)
    if not match or not any(match.groups()):
        return None
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    total = hours * 3600 + minutes * 60 + seconds
    return total if total > 0 else None


def split_time_and_message(text: str):
    """
    Expects format: <time> <message>
    e.g. "30m Call mom", "2h30m Meeting", "45s Take rice off"
    Returns (time_str, message) or (None, None)
    """
    parts = text.strip().split(maxsplit=1)
    if len(parts) < 2:
        return None, None
    time_str = parts[0]
    message = parts[1]
    seconds = parse_time_string(time_str)
    if not seconds:
        return None, None
    return time_str, message


def parse_reminder_time(time_str: str, timezone_str: str):
    """
    Converts time string to a future timezone-aware datetime.
    """
    seconds = parse_time_string(time_str)
    if not seconds:
        return None
    tz = pytz.timezone(timezone_str)
    now = datetime.now(tz)
    return now + timedelta(seconds=seconds)
