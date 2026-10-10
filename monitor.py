import base64
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from nacl.public import PublicKey, SealedBox


GRAPHQL_URL = "https://api.upwork.com/graphql"
TOKEN_URL = "https://www.upwork.com/api/v3/oauth2/token"

# Search terms used for Upwork public marketplace searches.
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
    "Geojson",
    "Kml",
]

# cron-job.org runs every 15 minutes. A 20-minute window gives a 5-minute overlap.
MAX_JOB_AGE_MINUTES = 20

# Keep seen job identifiers for 48 hours; fully reset the history every 72 hours.
SEEN_RETENTION_HOURS = 48
SEEN_CLEAN_INTERVAL_HOURS = 72

STATE_FILE = Path("seen_jobs.json")

# A job is relevant if any complete term below occurs in its title, description,
# or skills. This is binary filtering; no relevance score is calculated.
RELEVANCE_TERMS = [
    # Core GIS
    "gis", "geospatial", "geographic information system", "spatial analysis",
    "spatial data", "geospatial data", "geospatial analysis",
    # Esri / desktop GIS
    "arcgis", "arcgis pro", "arcgis online", "arcgis server", "arcgis portal",
    "arcgis javascript api", "arcpy", "qgis",
    # Web GIS / web mapping
    "web mapping", "web map", "interactive map", "interactive mapping",
    "interactive gis", "gis web application", "web map application",
    "mapbox", "mapbox gl js", "leaflet", "leafletjs", "d3.js",
    # GIS programming / data processing
    "python gis", "geopandas", "gdal", "postgis", "geoprocessing",
    "gis automation", "gis data processing", "spatial database", "cad to gis",
    "geojson", "shapefile", "kml", "openstreetmap", "osm",
    # Cartography / mapping
    "cartography", "gis cartography", "cartographic design", "map design",
    "thematic mapping", "map production", "cartographic visualization",
    "topographic mapping", "gis mapping",
    # Digitizing / GIS data production
    "map digitization", "map digitizing", "digitizing", "georeferencing",
    "georeference", "building footprints", "building digitization",
    "road digitization", "parcel digitization", "land use digitization",
    "utility digitization",
    # GeoAI / AI / machine learning
    "geoai", "geospatial ai", "ai gis", "gis ai", "ai geospatial",
    "machine learning gis", "machine learning geospatial", "deep learning gis",
    "deep learning geospatial", "geospatial machine learning",
    "computer vision gis", "geospatial computer vision", "geospatial data science",
    "arcgis deep learning", "arcgis pro deep learning", "object detection gis",
    "image segmentation gis", "semantic segmentation gis",
    "instance segmentation gis", "ocr gis", "geospatial ocr",
    # Remote sensing / imagery
    "remote sensing", "remote sensing ai", "remote sensing machine learning",
    "remote sensing deep learning", "satellite image analysis", "satellite imagery",
    "satellite imagery ai", "image classification gis", "object detection geospatial",
    # Other GIS-related tools
    "google earth", "geocoding",
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

    value = str(value).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def load_state():
    """Load and prune duplicate state while preserving the cleanup timestamp."""
    if not STATE_FILE.exists():
        return {}, None

    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}, None

        raw_jobs = data.get("jobs", {})
        if not isinstance(raw_jobs, dict):
            raw_jobs = {}

        last_cleanup = parse_dt(data.get("last_cleanup"))
        cutoff = utcnow() - timedelta(hours=SEEN_RETENTION_HOURS)
        cleaned = {}

        for job_id, timestamp in raw_jobs.items():
            dt = parse_dt(timestamp)
            if dt and dt >= cutoff:
                cleaned[str(job_id)] = timestamp

        return cleaned, last_cleanup

    except (OSError, json.JSONDecodeError, TypeError) as exc:
        print(f"Could not read state file; starting safely: {type(exc).__name__}")
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
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def update_github_refresh_secret(new_refresh_token):
    # Persist a rotated Upwork refresh token to GitHub Actions secrets with retries.
    github_token = os.getenv("GH_SECRETS_TOKEN")
    repository = os.getenv("GITHUB_REPOSITORY")
    if not github_token or not repository:
        raise RuntimeError(
            "Missing GH_SECRETS_TOKEN or GITHUB_REPOSITORY; cannot save a rotated token."
        )

    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {github_token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    base_url = f"https://api.github.com/repos/{repository}/actions/secrets"

    # Retry network errors, rate limits, and GitHub server errors.
    for attempt in range(1, 4):
        try:
            key_response = requests.get(
                f"{base_url}/public-key", headers=headers, timeout=20
            )
            key_response.raise_for_status()
            key_data = key_response.json()

            public_key = PublicKey(base64.b64decode(key_data["key"], validate=True))
            encrypted = SealedBox(public_key).encrypt(
                new_refresh_token.encode("utf-8")
            )
            response = requests.put(
                f"{base_url}/UPWORK_REFRESH_TOKEN",
                headers=headers,
                json={
                    "encrypted_value": base64.b64encode(encrypted).decode("ascii"),
                    "key_id": key_data["key_id"],
                },
                timeout=20,
            )
            response.raise_for_status()
            print("Updated UPWORK_REFRESH_TOKEN in GitHub Actions secrets.")
            return

        except (requests.RequestException, KeyError, ValueError, TypeError) as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            retryable = status is None or status == 429 or status >= 500
            if attempt < 3 and retryable:
                import time
                time.sleep(attempt * 2)
                continue

            if isinstance(exc, requests.RequestException):
                try:
                    details = exc.response.json().get("message", "")
                except (AttributeError, ValueError):
                    details = ""
                raise RuntimeError(
                    "Upwork returned a replacement refresh token, but GitHub "
                    f"could not save it after {attempt} attempt(s). "
                    f"HTTP status: {status}; details: {details or type(exc).__name__}. "
                    "Fix the GitHub API error before running the monitor again."
                ) from None

            raise RuntimeError(
                "Upwork returned a replacement refresh token, but GitHub's "
                "secret response was invalid. Fix the GitHub API response before "
                "running the monitor again. "
                f"Error type: {type(exc).__name__}."
            ) from None

    raise RuntimeError(
        "Could not save the rotated Upwork refresh token after three attempts."
    )


def get_access_token():
    """Return a configured access token or refresh it with OAuth credentials."""
    # Optional direct access token. If set, it takes precedence over refresh.
    direct_token = os.getenv("UPWORK_ACCESS_TOKEN")
    if direct_token:
        return direct_token

    client_id = os.getenv("UPWORK_CLIENT_ID")
    client_secret = os.getenv("UPWORK_CLIENT_SECRET")
    refresh_token = os.getenv("UPWORK_REFRESH_TOKEN")
    github_token = os.getenv("GH_SECRETS_TOKEN")

    if not all([client_id, client_secret, refresh_token]):
        raise RuntimeError(
            "Missing Upwork credentials. Set UPWORK_CLIENT_ID, "
            "UPWORK_CLIENT_SECRET, and UPWORK_REFRESH_TOKEN."
        )

    if not github_token:
        raise RuntimeError(
            "Missing GH_SECRETS_TOKEN. Add a GitHub token with repository "
            "Actions-secrets write permission as the GH_SECRETS_TOKEN secret."
        )

    # IMPORTANT: only one OAuth request is made. Do not log credential values
    # or the raw response body, which could contain sensitive data.
    try:
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
    except requests.RequestException as exc:
        raise RuntimeError(
            "Could not reach Upwork OAuth endpoint "
            f"({type(exc).__name__})."
        ) from None

    try:
        payload = response.json()
    except ValueError:
        payload = {}

    if not response.ok:
        error_code = payload.get("error")
        error_description = payload.get("error_description")
        details = []
        if error_code:
            details.append(f"error={error_code}")
        if error_description:
            details.append(f"description={error_description}")

        safe_details = "; ".join(details) or "No readable OAuth error was returned"
        raise RuntimeError(
            f"Upwork OAuth token refresh failed (HTTP {response.status_code}): "
            f"{safe_details}. Check UPWORK_CLIENT_ID, UPWORK_CLIENT_SECRET, "
            "and UPWORK_REFRESH_TOKEN in GitHub Actions Secrets."
        )

    access_token = payload.get("access_token")
    if not access_token:
        error_code = payload.get("error")
        error_description = payload.get("error_description")
        details = [
            f"error={error_code}" if error_code else "",
            f"description={error_description}" if error_description else "",
        ]
        safe_details = "; ".join(part for part in details if part)
        if not safe_details:
            safe_details = "response did not contain access_token"
        raise RuntimeError(
            f"Upwork OAuth returned an unexpected response: {safe_details}"
        )

    new_refresh_token = payload.get("refresh_token")
    if new_refresh_token and new_refresh_token != refresh_token:
        update_github_refresh_secret(new_refresh_token)

    return access_token


def search_upwork(token, term):
    variables = {
        "filter": {
            "searchExpression_eq": term,
            "pagination": {"pageOffset": 0, "pageSize": 50},
        }
    }

    response = requests.post(
        GRAPHQL_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={"query": QUERY, "variables": variables},
        timeout=45,
    )
    response.raise_for_status()
    payload = response.json()

    if payload.get("errors"):
        # GraphQL error details are not credentials, but keep them concise.
        raise RuntimeError(json.dumps(payload["errors"], indent=2))

    return (
        payload.get("data", {})
        .get("publicMarketplaceJobPostingsSearch", {})
        .get("jobs", [])
    )


def job_url(job):
    ciphertext = str(job.get("ciphertext") or "").strip()
    if ciphertext:
        ciphertext = ciphertext.lstrip("~")
        return f"https://www.upwork.com/jobs/~{ciphertext}"
    return "https://www.upwork.com/"


def is_relevant_job(text):
    normalized = text.lower()
    for term in RELEVANCE_TERMS:
        pattern = (
            rf"(?<![a-z0-9]){re.escape(term.lower())}(?![a-z0-9])"
        )
        if re.search(pattern, normalized):
            return True
    return False


def format_budget(job):
    # Budget fields are intentionally omitted from the current public query.
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
    seen, last_cleanup = load_state()
    now = utcnow()

    cutoff = now - timedelta(minutes=MAX_JOB_AGE_MINUTES)

    # Full cleanup every 72 hours (3 days).
    cleanup_due = (
        last_cleanup is None
        or now - last_cleanup >= timedelta(hours=SEEN_CLEAN_INTERVAL_HOURS)
    )
    if cleanup_due:
        print("72-hour seen-job cleanup is due; resetting duplicate history.")
        seen = {}
        last_cleanup = now

    jobs_by_id = {}
    errors = []

    for term in SEARCH_TERMS:
        try:
            jobs = search_upwork(token, term)
            for job in jobs:
                job_id = str(job.get("ciphertext") or job.get("recno") or "")
                if job_id:
                    jobs_by_id[job_id] = job
        except Exception as exc:
            errors.append(f"{term}: {type(exc).__name__}: {exc}")

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

        age_minutes = max(0, (now - published).total_seconds() / 60)

        skills = job.get("skills") or []
        skill_text = " ".join(
            f"{skill.get('name', '')} {skill.get('prettyName', '')}"
            for skill in skills
        )
        relevance_text = (
            f"{job.get('title', '')} "
            f"{job.get('description', '')} "
            f"{skill_text}"
        )

        if not is_relevant_job(relevance_text):
            continue

        new_matches.append((published, age_minutes, job_id, job))

    new_matches.sort(key=lambda item: item[0], reverse=True)

    messages = []
    for index, (_, age_minutes, job_id, job) in enumerate(new_matches, start=1):
        title = job.get("title") or "Untitled GIS job"
        description = " ".join((job.get("description") or "").split())
        if len(description) > 500:
            description = description[:497] + "..."

        skills = job.get("skills") or []
        skill_names = [
            skill.get("prettyName") or skill.get("name")
            for skill in skills
            if skill.get("prettyName") or skill.get("name")
        ]

        job_message = (
            f"{index}. {title}\n\n"
            f"Budget/Rate: {format_budget(job)}\n"
            f"Posted: {format_age(age_minutes)}\n"
        )
        if skill_names:
            job_message += "Skills: " + ", ".join(skill_names[:12]) + "\n"

        job_message += (
            f"\n{description}\n\n"
            f"Upwork: {job_url(job)}"
        )
        messages.append(job_message)

    # Send one combined notification. Mark jobs seen only after Telegram succeeds.
    if messages:
        message = (
            f"NEW GIS JOBS — {len(messages)}\n\n"
            + "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n".join(messages)
        )
        send_telegram(message)

        for _, _, job_id, _ in new_matches:
            seen[job_id] = now.isoformat()

    save_state(seen, last_cleanup)

    print(f"Checked {len(jobs_by_id)} unique jobs.")
    print(f"New qualifying jobs: {len(new_matches)}")
    print(f"Search errors: {len(errors)}")
    if cleanup_due:
        print(f"Seen-job history reset at: {last_cleanup.isoformat()}")


if __name__ == "__main__":
    main()
