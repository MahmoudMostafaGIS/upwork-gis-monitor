import os
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
import re

import requests


GRAPHQL_URL = "https://api.upwork.com/graphql"
TOKEN_URL = "https://www.upwork.com/api/v3/oauth2/token"


# ============================================================
# UPWORK SEARCH TERMS
# ============================================================

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


# ============================================================
# MONITOR SETTINGS
# ============================================================

# Search jobs posted within the last 20 minutes.
#
# cron-job.org runs every 10 minutes, so this gives a 10-minute
# overlap between consecutive runs.
MAX_JOB_AGE_MINUTES = 20


# Keep duplicate IDs for 48 hours.
SEEN_RETENTION_HOURS = 48


# Completely reset the seen-job history every 4 days.
#
# This prevents seen_jobs.json from growing indefinitely.
SEEN_CLEAN_INTERVAL_HOURS = 96


STATE_FILE = Path("seen_jobs.json")


# ============================================================
# LOCAL RELEVANCE TERMS
# ============================================================
#
# These are intentionally broader than SEARCH_TERMS.
#
# SEARCH_TERMS control how many Upwork API searches we make.
#
# RELEVANCE_TERMS perform the final local check on:
#   - title
#   - description
#   - skills
#
# A job is accepted if at least one complete relevance term
# appears in the job text.
# ============================================================

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

    # Digitizing / GIS data production
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


# ============================================================
# GRAPHQL QUERY
# ============================================================

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


# ============================================================
# TIME HELPERS
# ============================================================

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


# ============================================================
# STATE MANAGEMENT
# ============================================================

def load_state():
    """
    Load duplicate-job state.

    The state file contains:
        {
            "last_cleanup": "...",
            "jobs": {
                "job_id": "timestamp"
            }
        }

    Old state files containing last_successful_check are also handled
    safely because only the "jobs" section is required.
    """

    if not STATE_FILE.exists():
        return {}, None

    try:
        data = json.loads(
            STATE_FILE.read_text(encoding="utf-8")
        )

        if not isinstance(data, dict):
            return {}, None

        raw_jobs = data.get("jobs", {})
        last_cleanup = parse_dt(
            data.get("last_cleanup")
        )

        if not isinstance(raw_jobs, dict):
            raw_jobs = {}

        # Keep only jobs seen within the retention period.
        cutoff = utcnow() - timedelta(
            hours=SEEN_RETENTION_HOURS
        )

        cleaned = {}

        for job_id, timestamp in raw_jobs.items():

            dt = parse_dt(timestamp)

            if dt and dt >= cutoff:
                cleaned[str(job_id)] = timestamp

        return cleaned, last_cleanup

    except Exception as exc:

        print(
            f"Could not read state file; "
            f"starting safely: {exc}"
        )

        return {}, None


def save_state(seen, last_cleanup=None):

    payload = {
        "last_cleanup": (
            last_cleanup.isoformat()
            if isinstance(last_cleanup, datetime)
            else last_cleanup
        ),
        "jobs": seen,
    }

    STATE_FILE.write_text(
        json.dumps(
            payload,
            indent=2,
            sort_keys=True
        ),
        encoding="utf-8",
    )


# ============================================================
# UPWORK AUTHENTICATION
# ============================================================

def get_access_token():

    # Optional direct access token.
    token = os.getenv("UPWORK_ACCESS_TOKEN")

    if token:
        return token

    client_id = os.getenv("UPWORK_CLIENT_ID")
    client_secret = os.getenv("UPWORK_CLIENT_SECRET")
    refresh_token = os.getenv("UPWORK_REFRESH_TOKEN")

    if not all([
        client_id,
        client_secret,
        refresh_token
    ]):

        raise RuntimeError(
            "Missing Upwork credentials. "
            "Set UPWORK_CLIENT_ID + "
            "UPWORK_CLIENT_SECRET + "
            "UPWORK_REFRESH_TOKEN."
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

        raise RuntimeError(
            f"Upwork token refresh failed: {payload}"
        )

    return token


# ============================================================
# UPWORK SEARCH
# ============================================================

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

        raise RuntimeError(
            json.dumps(
                payload["errors"],
                indent=2
            )
        )

    return (
        payload
        .get("data", {})
        .get(
            "publicMarketplaceJobPostingsSearch",
            {}
        )
        .get("jobs", [])
    )


# ============================================================
# JOB URL
# ============================================================

def job_url(job):

    ciphertext = str(
        job.get("ciphertext") or ""
    ).strip()

    if ciphertext:

        # Upwork can return ciphertext with "~".
        # The final URL must contain exactly one "~".
        ciphertext = ciphertext.lstrip("~")

        return (
            f"https://www.upwork.com/jobs/~"
            f"{ciphertext}"
        )

    return "https://www.upwork.com/"


# ============================================================
# RELEVANCE FILTER
# ============================================================

def is_relevant_job(text):

    """
    Return True when a complete GIS-related term
    appears in the supplied text.

    Word-boundary protection prevents:
        GIS
    from incorrectly matching:
        logistics
    """

    normalized = text.lower()

    for term in RELEVANCE_TERMS:

        pattern = (
            rf"(?<![a-z0-9])"
            rf"{re.escape(term.lower())}"
            rf"(?![a-z0-9])"
        )

        if re.search(pattern, normalized):
            return True

    return False


# ============================================================
# FORMATTING
# ============================================================

def format_budget(job):

    # Public marketplace search currently does not provide
    # reliable budget fields for this monitor.

    return "Budget not available from public search"


def format_age(minutes):

    if minutes < 1:
        return "less than 1 minute ago"

    return f"{int(minutes)} minutes ago"


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    bot_token = os.getenv(
        "TELEGRAM_BOT_TOKEN"
    )

    chat_id = os.getenv(
        "TELEGRAM_CHAT_ID"
    )

    if not bot_token or not chat_id:

        raise RuntimeError(
            "Telegram secrets are not configured. "
            "Set TELEGRAM_BOT_TOKEN and "
            "TELEGRAM_CHAT_ID."
        )

    url = (
        f"https://api.telegram.org/"
        f"bot{bot_token}/sendMessage"
    )

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


# ============================================================
# MAIN
# ============================================================

def main():

    token = get_access_token()

    seen, last_cleanup = load_state()

    now = utcnow()

    # --------------------------------------------------------
    # Normal polling window
    # --------------------------------------------------------
    #
    # cron-job.org runs every 10 minutes.
    #
    # 20 minutes gives us a full overlap between runs.
    #
    cutoff = (
        now
        - timedelta(
            minutes=MAX_JOB_AGE_MINUTES
        )
    )

    # --------------------------------------------------------
    # Four-day cleanup
    # --------------------------------------------------------
    #
    # Every 96 hours the duplicate history is completely reset.
    #
    # This is safe because we only search the last 20 minutes.
    #
    cleanup_due = (
        last_cleanup is None
        or
        now - last_cleanup
        >= timedelta(
            hours=SEEN_CLEAN_INTERVAL_HOURS
        )
    )

    if cleanup_due:

        print(
            "Four-day seen-job cleanup is due; "
            "resetting duplicate history."
        )

        seen = {}

        last_cleanup = now

    # --------------------------------------------------------
    # Search Upwork
    # --------------------------------------------------------

    jobs_by_id = {}

    errors = []

    for term in SEARCH_TERMS:

        try:

            jobs = search_upwork(
                token,
                term
            )

            for job in jobs:

                job_id = str(
                    job.get("ciphertext")
                    or job.get("recno")
                    or ""
                )

                if job_id:

                    jobs_by_id[job_id] = job

        except Exception as exc:

            errors.append(
                f"{term}: {exc}"
            )

    # --------------------------------------------------------
    # Search errors
    # --------------------------------------------------------

    if errors:

        print(
            "Some searches failed:"
        )

        for error in errors:

            print(error)

    # --------------------------------------------------------
    # Filter new jobs
    # --------------------------------------------------------

    new_matches = []

    for job_id, job in jobs_by_id.items():

        # Already notified.
        if job_id in seen:
            continue

        published = parse_dt(
            job.get("createdDateTime")
        )

        if not published:
            continue

        # Ignore future timestamps caused by API clock differences.
        if published > (
            now + timedelta(minutes=2)
        ):
            continue

        # Ignore jobs outside our polling window.
        if published < cutoff:
            continue

        age_minutes = max(
            0,
            (
                now - published
            ).total_seconds()
            / 60,
        )

        # ----------------------------------------------------
        # Build relevance text
        # ----------------------------------------------------

        relevance_text = (
            f"{job.get('title', '')} "
            f"{job.get('description', '')} "
            + " ".join(
                (
                    s.get("name", "")
                    + " "
                    + s.get(
                        "prettyName",
                        ""
                    )
                )
                for s in (
                    job.get("skills")
                    or []
                )
            )
        )

        # ----------------------------------------------------
        # Final GIS relevance check
        # ----------------------------------------------------

        if not is_relevant_job(
            relevance_text
        ):
            continue

        new_matches.append(
            (
                published,
                age_minutes,
                job_id,
                job,
            )
        )

    # Newest jobs first.
    new_matches.sort(
        key=lambda x: x[0],
        reverse=True
    )

    # ========================================================
    # BUILD TELEGRAM MESSAGE
    # ========================================================

    messages = []

    for index, (
        published,
        age_minutes,
        job_id,
        job,
    ) in enumerate(
        new_matches,
        start=1
    ):

        title = job.get(
            "title",
            "Untitled GIS job"
        )

        # ----------------------------------------------------
        # Description
        # ----------------------------------------------------

        description = (
            job.get("description")
            or ""
        ).strip()

        description = " ".join(
            description.split()
        )

        # Keep Telegram messages manageable.
        if len(description) > 500:

            description = (
                description[:497]
                + "..."
            )

        # ----------------------------------------------------
        # Skills
        # ----------------------------------------------------

        skills = (
            job.get("skills")
            or []
        )

        skill_names = [

            s.get("prettyName")
            or s.get("name")

            for s in skills

            if (
                s.get("prettyName")
                or s.get("name")
            )
        ]

        # ----------------------------------------------------
        # Job message
        # ----------------------------------------------------

        job_message = (

            f"{index}. {title}\n\n"

            f"Budget/Rate: "
            f"{format_budget(job)}\n"

            f"Posted: "
            f"{format_age(age_minutes)}\n"
        )

        if skill_names:

            job_message += (
                "Skills: "
                + ", ".join(
                    skill_names[:12]
                )
                + "\n"
            )

        job_message += (

            f"\n{description}\n\n"

            f"Upwork: "
            f"{job_url(job)}"
        )

        messages.append(
            job_message
        )

    # ========================================================
    # SEND TELEGRAM
    # ========================================================

    if messages:

        message = (
            "NEW GIS JOBS — "
            + str(len(messages))
            + "\n\n"
        )

        message += (
            "\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        ).join(messages)

        # ----------------------------------------------------
        # IMPORTANT
        # ----------------------------------------------------
        #
        # Jobs are marked as seen ONLY after Telegram
        # successfully accepts the message.
        #
        # If Telegram fails, this raises an exception and
        # the jobs remain unseen for the next run.
        #
        send_telegram(message)

        for (
            _,
            _,
            job_id,
            _
        ) in new_matches:

            seen[job_id] = (
                now.isoformat()
            )

    # ========================================================
    # SAVE STATE
    # ========================================================

    save_state(
        seen,
        last_cleanup
    )

    # ========================================================
    # LOGGING
    # ========================================================

    print(
        f"Checked "
        f"{len(jobs_by_id)} "
        f"unique jobs."
    )

    print(
        f"New qualifying jobs: "
        f"{len(new_matches)}"
    )

    print(
        f"Search errors: "
        f"{len(errors)}"
    )

    if cleanup_due:

        print(
            "Seen-job history reset at: "
            f"{last_cleanup.isoformat()}"
        )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()