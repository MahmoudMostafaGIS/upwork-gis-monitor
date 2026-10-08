import os
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
import re

import requests

GRAPHQL_URL = "https://api.upwork.com/graphql"
TOKEN_URL = "https://www.upwork.com/api/v3/oauth2/token"

SEARCH_TERMS = [
    # Core GIS
    "GIS",
    "Geospatial",
    "Geographic Information System",
    "Spatial Analysis",

    # Esri / desktop GIS
    "ArcGIS",
    "ArcGIS Pro",
    "QGIS",
    "ArcPy",

    # Web mapping / visualization
    "Web Mapping",
    "Interactive Map",
    "Mapbox",
    "Leaflet",
    "D3.js",

    # Geospatial development / data
    "GeoPandas",
    "GDAL",
    "PostGIS",
    "OpenStreetMap",
    "Geoprocessing",

    # Cartography / data production
    "Cartography",
    "Map Digitization",
    "Georeferencing",

    # GeoAI / remote sensing
    "GeoAI",
    "Geospatial AI",
    "Remote Sensing",
    "Satellite Image Analysis",

    # Other useful GIS tools / formats
    "Google Earth",
    "Shapefile",
]

# Normal polling window. GitHub Actions runs every 10 minutes, so a small
# overlap helps protect against scheduling/API delays.
MAX_JOB_AGE_MINUTES = 20

# Keep duplicate history long enough to cover delayed runs and recovery scans.
SEEN_RETENTION_HOURS = 48

# When a run is delayed or missed, search back from the previous successful
# check (with a small overlap) instead of relying only on the normal 20-minute
# window. The cap prevents an extremely old state from causing an unbounded
# recovery window.
RECOVERY_OVERLAP_MINUTES = 5
MAX_RECOVERY_LOOKBACK_HOURS = 24

STATE_FILE = Path("seen_jobs.json")


RELEVANCE_TERMS = [
    # Core GIS
    "gis",
    "geospatial",
    "geographic information system",
    "spatial analysis",
    "spatial data",
    "geospatial data",
    "geospatial analysis",

    # Esri / desktop GIS
    "arcgis",
    "arcgis pro",
    "arcgis online",
    "arcgis server",
    "arcgis portal",
    "arcgis javascript api",
    "arcpy",
    "qgis",

    # Web GIS / web mapping
    "web mapping",
    "web map",
    "interactive map",
    "interactive mapping",
    "interactive gis",
    "gis web application",
    "web map application",
    "mapbox",
    "mapbox gl js",
    "leaflet",
    "leafletjs",
    "d3.js",

    # GIS programming / data processing
    "python gis",
    "geopandas",
    "gdal",
    "postgis",
    "geoprocessing",
    "gis automation",
    "gis data processing",
    "spatial database",
    "cad to gis",
    "geojson",
    "shapefile",
    "kml",
    "openstreetmap",
    "osm",

    # Cartography / mapping
    "cartography",
    "gis cartography",
    "cartographic design",
    "map design",
    "thematic mapping",
    "map production",
    "cartographic visualization",
    "topographic mapping",
    "gis mapping",
    "map digitization",
    "map digitizing",
    "digitizing",
    "georeferencing",
    "georeference",
    "building footprints",
    "building digitization",
    "road digitization",
    "parcel digitization",
    "land use digitization",
    "utility digitization",

    # GeoAI / AI / machine learning
    "geoai",
    "geospatial ai",
    "ai gis",
    "gis ai",
    "ai geospatial",
    "machine learning gis",
    "machine learning geospatial",
    "deep learning gis",
    "deep learning geospatial",
    "geospatial machine learning",
    "computer vision gis",
    "geospatial computer vision",
    "geospatial data science",
    "arcgis deep learning",
    "arcgis pro deep learning",
    "object detection gis",
    "image segmentation gis",
    "semantic segmentation gis",
    "instance segmentation gis",
    "ocr gis",
    "geospatial ocr",

    # Remote sensing / imagery
    "remote sensing",
    "remote sensing ai",
    "remote sensing machine learning",
    "remote sensing deep learning",
    "satellite image analysis",
    "satellite imagery",
    "satellite imagery ai",
    "image classification gis",
    "object detection geospatial",

    # Other GIS-related tools
    "google earth",
    "geocoding",
]

QUERY = """
query PublicSearch($filter: PublicMarketplaceJobPostingsSearchFilter!) {
  publicMarketplaceJobPostingsSearch(
    marketPlaceJobFilter: $filter
  ) {
    jobs {
      title
      createdDateTime
      type
      ciphertext
      recno
      description
      skills {
        name
        prettyName
      }
      category
      subcategory
    }
  }
}
"""


def utcnow():
    return datetime.now(timezone.utc)


def parse_dt(value):
    if not value:
        return None

    value = value.replace("Z", "+00:00")

    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def load_state():
    """Load duplicate state and the timestamp of the last successful run.

    Supports the old flat ``{job_id: timestamp}`` format so upgrading the
    monitor does not discard existing duplicate history.
    """
    if not STATE_FILE.exists():
        return {}, None

    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))

        # New state format.
        if isinstance(data, dict) and "jobs" in data:
            raw_jobs = data.get("jobs", {})
            last_successful_check = data.get("last_successful_check")
        # Backward compatibility with the previous flat state format.
        elif isinstance(data, dict):
            raw_jobs = data
            last_successful_check = None
        else:
            return {}, None

        if not isinstance(raw_jobs, dict):
            raw_jobs = {}

        cutoff = utcnow() - timedelta(hours=SEEN_RETENTION_HOURS)
        cleaned = {}

        for job_id, timestamp in raw_jobs.items():
            dt = parse_dt(timestamp)

            if dt and dt >= cutoff:
                cleaned[str(job_id)] = timestamp

        last_check = parse_dt(last_successful_check)

        return cleaned, last_check

    except Exception as exc:
        print(f"Could not read state file; starting safely: {exc}")
        return {}, None


def save_state(seen, last_successful_check=None):
    payload = {
        "last_successful_check": (
            last_successful_check.isoformat()
            if isinstance(last_successful_check, datetime)
            else last_successful_check
        ),
        "jobs": seen,
    }

    STATE_FILE.write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def get_access_token():
    token = os.getenv("UPWORK_ACCESS_TOKEN")

    if token:
        return token

    client_id = os.getenv("UPWORK_CLIENT_ID")
    client_secret = os.getenv("UPWORK_CLIENT_SECRET")
    refresh_token = os.getenv("UPWORK_REFRESH_TOKEN")

    if not all([client_id, client_secret, refresh_token]):
        raise RuntimeError(
            "Missing Upwork credentials. Set "
            "UPWORK_CLIENT_ID + UPWORK_CLIENT_SECRET + UPWORK_REFRESH_TOKEN."
        )

    response = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
        },
        timeout=30,
    )

    response.raise_for_status()

    payload = response.json()
    token = payload.get("access_token")

    if not token:
        raise RuntimeError(f"Upwork token refresh failed: {payload}")

    return token


def search_upwork(token, term):
    variables = {
        "filter": {
            "searchExpression_eq": term,
            "pagination": {
                "pageOffset": 0,
                "pageSize": 50,
            },
        }
    }

    response = requests.post(
        GRAPHQL_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={
            "query": QUERY,
            "variables": variables,
        },
        timeout=45,
    )

    response.raise_for_status()

    payload = response.json()

    if payload.get("errors"):
        raise RuntimeError(json.dumps(payload["errors"], indent=2))

    return (
        payload.get("data", {})
        .get("publicMarketplaceJobPostingsSearch", {})
        .get("jobs", [])
    )


def job_url(job):
    ciphertext = str(job.get("ciphertext") or "").strip()

    if ciphertext:
        # Upwork's public API may return ciphertext with a leading "~".
        # The job URL itself must contain exactly one "~".
        ciphertext = ciphertext.lstrip("~")
        return f"https://www.upwork.com/jobs/~{ciphertext}"

    return "https://www.upwork.com/"


def is_relevant_job(text):
    """Return True only when a complete GIS-related term appears.

    Word boundaries are important here. A simple substring check for "gis"
    would incorrectly match unrelated words such as "logistics".
    """
    normalized = text.lower()

    for term in RELEVANCE_TERMS:
        pattern = rf"(?<![a-z0-9]){re.escape(term.lower())}(?![a-z0-9])"
        if re.search(pattern, normalized):
            return True

    return False


def format_budget(job):
    return "Budget not available from public search"


def format_age(minutes):
    if minutes < 1:
        return "less than 1 minute ago"

    return f"{int(minutes)} minutes ago"


def send_telegram(message):
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not bot_token or not chat_id:
        raise RuntimeError(
        "Telegram secrets are not configured. "
        "Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID."
    )

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    response = requests.post(
        url,
        json={
            "chat_id": chat_id,
            "text": message,
            "disable_web_page_preview": False,
        },
        timeout=30,
    )

    response.raise_for_status()


def main():
    token = get_access_token()
    seen, last_successful_check = load_state()

    now = utcnow()

    # Normal window: 20 minutes.
    cutoff = now - timedelta(minutes=MAX_JOB_AGE_MINUTES)

    # Automatic missed-job recovery: if the previous successful run was
    # farther back than the normal window, include jobs from shortly before
    # that run as well. This catches jobs missed because GitHub Actions ran
    # late, was temporarily unavailable, or an API call was delayed.
    recovery_floor = now - timedelta(hours=MAX_RECOVERY_LOOKBACK_HOURS)

    if last_successful_check:
        recovery_cutoff = max(
            recovery_floor,
            last_successful_check - timedelta(minutes=RECOVERY_OVERLAP_MINUTES),
        )

        if recovery_cutoff < cutoff:
            cutoff = recovery_cutoff
            print(
                "Recovery mode: searching back to "
                f"{cutoff.isoformat()} based on the previous successful run."
            )

    jobs_by_id = {}
    errors = []

    for term in SEARCH_TERMS:
        try:
            for job in search_upwork(token, term):
                job_id = str(
                    job.get("ciphertext")
                    or job.get("recno")
                    or ""
                )

                if job_id:
                    jobs_by_id[job_id] = job

        except Exception as exc:
            errors.append(f"{term}: {exc}")

    if errors:
        print("Some searches failed:")

        for error in errors:
            print(error)

    new_matches = []

    for job_id, job in jobs_by_id.items():
        if job_id in seen:
            continue

        published = parse_dt(job.get("createdDateTime"))

        if not published:
            continue

        if published > now + timedelta(minutes=2):
            continue

        if published < cutoff:
            continue

        age_minutes = max(
            0,
            (now - published).total_seconds() / 60,
        )

        # Upwork's public search can return broad/irrelevant results.
        # Verify the actual title, description, and skills before notifying.
        relevance_text = (
            f"{job.get('title', '')} "
            f"{job.get('description', '')} "
            + " ".join(
                (s.get("name", "") + " " + s.get("prettyName", ""))
                for s in (job.get("skills") or [])
            )
        ).lower()

        if not is_relevant_job(relevance_text):
            continue

        new_matches.append(
            (
                published,
                age_minutes,
                job_id,
                job,
            )
        )

    new_matches.sort(key=lambda x: x[0], reverse=True)

    messages = []

    for index, (
        published,
        age_minutes,
        job_id,
        job,
    ) in enumerate(new_matches, start=1):

        title = job.get("title", "Untitled GIS job")

        description = (job.get("description") or "").strip()
        description = " ".join(description.split())

        if len(description) > 500:
            description = description[:497] + "..."

        skills = job.get("skills") or []

        skill_names = [
            s.get("prettyName") or s.get("name")
            for s in skills
            if s.get("prettyName") or s.get("name")
        ]

        job_message = (
            f"{index}. {title}\n\n"
            f"Budget/Rate: {format_budget(job)}\n"
            f"Posted: {format_age(age_minutes)}\n"
        )

        if skill_names:
            job_message += f"Skills: {', '.join(skill_names[:12])}\n"

        job_message += (
            f"\n{description}\n\n"
            f"Upwork: {job_url(job)}"
        )

        messages.append(job_message)

    if messages:
        # One Telegram notification per monitor run, with a double-line
        # separator only between jobs (not before the first or after the last).
        message = "NEW GIS JOBS — " + str(len(messages)) + "\n\n"
        message += "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n".join(messages)

        # IMPORTANT: only mark jobs as seen after Telegram accepts the
        # notification. If Telegram fails, the jobs remain unseen and will be
        # retried on the next run instead of being permanently lost.
        send_telegram(message)

        for _, _, job_id, _ in new_matches:
            seen[job_id] = now.isoformat()

    # Advance the recovery checkpoint only when every Upwork search succeeded.
    # If one or more searches failed, keeping the previous checkpoint allows
    # the next run to recover jobs that may have been missed.
    if not errors:
        last_successful_check = now

    save_state(seen, last_successful_check)

    print(f"Checked {len(jobs_by_id)} unique jobs.")
    print(f"New qualifying jobs: {len(new_matches)}")
    print(f"Search errors: {len(errors)}")
    if last_successful_check:
        print(f"Last successful check: {last_successful_check.isoformat()}")


if __name__ == "__main__":
    main()
