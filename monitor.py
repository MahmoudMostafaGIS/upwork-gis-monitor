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
    "GIS Analyst",
    "Geographic Information System (GIS)",
    "Geographic Information System",
    "ArcGIS",
    "ArcGIS Pro",
    "QGIS",
    "Geospatial",
    "Geospatial Data",
    "Geospatial Analysis",
    "Spatial Analysis",

    # GIS development / web mapping
    "GIS Web Development",
    "Web GIS",
    "Web Mapping",
    "Web Map",
    "Interactive Map",
    "Interactive Mapping",
    "Interactive GIS",
    "GIS Web Application",
    "Web Map Application",
    "Mapbox",
    "Mapbox GL JS",
    "Leaflet",
    "LeafletJS",
    "D3.js",
    "ArcGIS JavaScript API",
    "ArcGIS Online",

    # GIS data / automation
    "Python GIS",
    "ArcPy",
    "GeoPandas",
    "GDAL",
    "PostGIS",
    "OpenStreetMap",
    "OSM",
    "GIS Automation",
    "Spatial Database",
    "GIS Data Processing",
    "CAD to GIS",

    # Digitizing / vectorization
    "Digitizing",
    "GIS Digitizing",
    "GIS Digitization",
    "Map Digitization",
    "Map Digitizing",
    "Building Digitization",
    "Building Footprints",
    "Road Digitization",
    "Parcel Digitization",
    "Land Use Digitization",
    "Utility Digitization",
    "Vectorization",
    "Raster to Vector",
    "Feature Digitization",
    "Georeferencing",
    "Georeference",

    # Cartography / mapping
    "Cartography",
    "GIS Cartography",
    "Cartographic Design",
    "Map Design",
    "Thematic Mapping",
    "Map Production",
    "Cartographic Visualization",
    "Topographic Mapping",
    "GIS Mapping",

    # GeoAI / AI / machine learning
    "GeoAI",
    "Geospatial AI",
    "AI GIS",
    "GIS AI",
    "AI Geospatial",
    "Machine Learning GIS",
    "Machine Learning Geospatial",
    "Deep Learning GIS",
    "Deep Learning Geospatial",
    "Geospatial Machine Learning",
    "Computer Vision GIS",
    "Geospatial Computer Vision",
    "Geospatial Data Science",
    "ArcGIS Deep Learning",
    "ArcGIS Pro Deep Learning",
    "Object Detection GIS",
    "Image Segmentation GIS",
    "Semantic Segmentation GIS",
    "Instance Segmentation GIS",
    "OCR GIS",
    "Geospatial OCR",

    # Remote sensing / imagery
    "Remote Sensing",
    "Remote Sensing AI",
    "Remote Sensing Machine Learning",
    "Remote Sensing Deep Learning",
    "Satellite",
    "Satellite Image Analysis",
    "Satellite Imagery AI",
    "Image Classification GIS",
    "Object Detection Geospatial",

    # Other useful GIS data terms
    "Geocoding",
    "Google Earth",
    "GeoJSON",
    "KML",
    "Shapefile",
]

MAX_JOB_AGE_MINUTES = 10
SEEN_RETENTION_HOURS = 24
STATE_FILE = Path("seen_jobs.json")


RELEVANCE_TERMS = [
    # Core GIS
    "gis",
    "gis analyst",
    "geographic information system",
    "arcgis",
    "arcgis pro",
    "qgis",
    "geospatial",
    "geospatial data",
    "geospatial analysis",
    "spatial analysis",

    # Web GIS / interactive mapping
    "gis web development",
    "web gis",
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
    "arcgis javascript api",
    "arcgis online",

    # GIS data / automation
    "python gis",
    "arcpy",
    "geopandas",
    "gdal",
    "postgis",
    "openstreetmap",
    "osm",
    "gis automation",
    "spatial database",
    "gis data processing",
    "cad to gis",

    # Digitizing / vectorization
    "digitizing",
    "gis digitizing",
    "gis digitization",
    "map digitization",
    "map digitizing",
    "building digitization",
    "building footprints",
    "road digitization",
    "parcel digitization",
    "land use digitization",
    "utility digitization",
    "vectorization",
    "raster to vector",
    "feature digitization",
    "georeferencing",
    "georeference",

    # Cartography
    "cartography",
    "gis cartography",
    "cartographic design",
    "map design",
    "thematic mapping",
    "map production",
    "cartographic visualization",
    "topographic mapping",
    "gis mapping",

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
    "satellite imagery ai",
    "image classification gis",
    "object detection geospatial",

    # Other GIS data terms
    "geocoding",
    "google earth",
    "geojson",
    "kml",
    "shapefile",
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


def load_seen():
    if not STATE_FILE.exists():
        return {}

    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))

        if not isinstance(data, dict):
            return {}

        cutoff = utcnow() - timedelta(hours=SEEN_RETENTION_HOURS)
        cleaned = {}

        for job_id, timestamp in data.items():
            dt = parse_dt(timestamp)

            if dt and dt >= cutoff:
                cleaned[job_id] = timestamp

        return cleaned

    except Exception:
        return {}


def save_seen(seen):
    STATE_FILE.write_text(
        json.dumps(seen, indent=2, sort_keys=True),
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
        print("Telegram secrets are not configured; printing alert instead.")
        print(message)
        return

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
    seen = load_seen()

    now = utcnow()
    cutoff = now - timedelta(minutes=MAX_JOB_AGE_MINUTES)

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

    for _, _, job_id, _ in new_matches:
        seen[job_id] = now.isoformat()

    save_seen(seen)

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
        send_telegram(message)

    print(f"Checked {len(jobs_by_id)} unique jobs.")
    print(f"New qualifying jobs: {len(new_matches)}")


if __name__ == "__main__":
    main()
