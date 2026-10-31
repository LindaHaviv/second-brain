"""Pure helpers for LinkedIn post ids — no DB, no network, safe to import anywhere.

Every LinkedIn feed URN (activity, ugcPost, share) is a 64-bit Snowflake-style id:
the top 41 bits are the creation time in Unix milliseconds, the low 22 bits are
sequence/shard. So any URL that carries the id also carries the exact publish
timestamp. That matters here because two importers (linkedin.py, linkedin_harvest.py)
only ever had a relative label ("3yr") to work from and stored approximate dates —
"N years ago" collapsed onto the import day, off by up to a year.

Verified against this brain's own rows: for the 60 posts the Apify actor dated
exactly, the decoded id matched the actor's timestamp within 1 second (the actor
truncates milliseconds). ugcPost/share ids are documented to use the same layout;
the brain holds none yet, so that part rests on documentation, not on data.

Both URL shapes LinkedIn hands out are covered:
  https://www.linkedin.com/feed/update/urn:li:activity:6050442574873796609/
  https://www.linkedin.com/posts/<handle>_<slug>-activity-7517687465206394880-L2iY
"""
import datetime
import re

_ID_RX = re.compile(r"(?:activity|ugcPost|share)[:\-](\d{19})(?!\d)", re.IGNORECASE)
_TIMESTAMP_BITS = 22
_EARLIEST = datetime.datetime(2003, 1, 1, tzinfo=datetime.timezone.utc)   # LinkedIn launched


def post_id_from_url(url):
    """'…urn:li:activity:6050442574873796609/' -> 6050442574873796609; None if absent."""
    m = _ID_RX.search(url or "")
    return int(m.group(1)) if m else None


def datetime_from_post_id(post_id):
    """Snowflake decode: id >> 22 = Unix ms -> aware UTC datetime (None if implausible)."""
    ms = int(post_id) >> _TIMESTAMP_BITS
    when = datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc)
    now = datetime.datetime.now(datetime.timezone.utc)
    if when < _EARLIEST or when > now + datetime.timedelta(days=1):
        return None
    return when


def published_at_from_url(url):
    """Exact UTC publish time encoded in a LinkedIn post URL, or None when the URL
    carries no id (e.g. the synthetic lh-<hash> URLs the JSON importer mints)."""
    pid = post_id_from_url(url)
    return datetime_from_post_id(pid) if pid else None
