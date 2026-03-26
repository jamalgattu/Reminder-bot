# parser.py
import re
from datetime import datetime, timedelta
import pytz

COUNTRY_TIMEZONE_MAP = {
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
    "usa": "America/New_York",
    "united states": "America/New_York",
    "canada": "America/Toronto",
    "mexico": "America/Mexico_City",
    "brazil": "America/Sao_Paulo",
    "argentina": "America/Argentina/Buenos_Aires",
    "colombia": "America/Bogota",
    "chile": "America/Santiago",
    "peru": "America/Lima",
    "nigeria": "Africa/Lagos",
    "kenya": "Africa/Nairobi",
    "south africa": "Africa/Johannesburg",
    "egypt": "Africa/Cairo",
    "ghana": "Africa/Accra",
    "australia": "Australia/Sydney",
    "new zealand": "Pacific/Auckland",
}

def parse_time_string(time_str: str):
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

def parse_time_to_dt(time_str, user_timezone):
    tz = pytz.timezone(user_timezone)
    now = datetime.now(tz)

    seconds = parse_time_string(time_str)
    if seconds:
        return now + timedelta(seconds=seconds)

    try:
        remind_time = datetime.strptime(time_str, "%H:%M").time()
        remind_dt = tz.localize(datetime.combine(now.date(), remind_time))
        if remind_dt <= now:
            remind_dt += timedelta(days=1)
        return remind_dt
    except ValueError:
        pass

    return None
