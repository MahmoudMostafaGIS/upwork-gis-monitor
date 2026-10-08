STATE_FILE = Path("seen_jobs.json")

SCORED_KEYWORDS = {
    "gis": 10,
    "arcgis": 10,
    "arcgis pro": 10,
    "qgis": 10,
    "mapbox": 10,
    "gis web": 10,
    "web gis": 10,
    "geospatial": 9,
    "spatial analysis": 8,
    "gis analysis": 10,
    "gis digitizing": 10,
    "gis digitization": 10,
    "map digitization": 9,
    "digitizing": 7,
    "vectorization": 8,
    "raster to vector": 8,
    "georeferencing": 8,
    "arcpy": 8,
    "geopandas": 8,
    "gdal": 7,
    "postgis": 8,
    "remote sensing": 7,
    "cartography": 6,
    "shapefile": 5,
    "geojson": 5,
    "spatial data": 7,
    "geographic information": 8,
    "feature extraction": 7,
    "building digitization": 8,
    "road digitization": 8,
    "parcel digitization": 8,
}

# Uses Upwork's public marketplace search endpoint.
# This endpoint requires "Read public marketplace Job Postings".
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
      amount {
        displayValue
        currency
      }
      hourlyBudgetType
      hourlyBudgetMin
      hourlyBudgetMax
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
    # Optional fallback. The current GitHub setup uses the refresh-token path.
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
    # PublicMarketplaceJobPostingsSearchFilter uses pageOffset/pageSize.
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
            json.dumps(payload["errors"], indent=2)
        )