import os
import re
import time
import requests
from urllib.parse import urlparse
from log_lib import log

OVERPASS_URL = "https://overpass.kumi.systems/api/interpreter"
PHOTON_URL = "https://photon.komoot.io/api/"


def _geocode_city(city, state, log_path):
    """Return (south, north, west, east) bounding box for the city via Photon geocoder."""
    q = f"{city}, {state}" if state else city
    try:
        resp = requests.get(
            PHOTON_URL,
            params={"q": q, "limit": 1, "lang": "en"},
            headers={"User-Agent": "LeadGenScript/1.0"},
            timeout=15,
        )
        features = resp.json().get("features", [])
        if not features:
            log(f"Photon: no results for '{q}'", log_path)
            return None
        # extent = [west, north, east, south]
        extent = features[0].get("properties", {}).get("extent")
        if extent and len(extent) == 4:
            w, n, e, s = extent
        else:
            # Fall back to point ± small padding
            coords = features[0].get("geometry", {}).get("coordinates", [])
            if not coords:
                return None
            lon, lat = coords
            w, e, s, n = lon - 0.1, lon + 0.1, lat - 0.1, lat + 0.1
        # Expand tiny bounding boxes (small town) by ~5 km
        if (n - s) < 0.05:
            s -= 0.05; n += 0.05
        if (e - w) < 0.05:
            w -= 0.05; e += 0.05
        log(f"Photon bbox for '{q}': s={s:.3f} n={n:.3f} w={w:.3f} e={e:.3f}", log_path)
        return s, n, w, e
    except Exception as exc:
        log(f"Photon geocode failed for '{q}': {exc}", log_path)
        return None


def _overpass_lawyers(south, north, west, east, log_path):
    """Query OSM Overpass for lawyer offices inside the bounding box, with retries."""
    bbox = f"{south},{west},{north},{east}"
    query = (
        f'[out:json][timeout:60];'
        f'('
        f'node["office"="lawyer"]({bbox});'
        f'way["office"="lawyer"]({bbox});'
        f'node["office"="law_firm"]({bbox});'
        f'way["office"="law_firm"]({bbox});'
        f'node["amenity"="lawyer"]({bbox});'
        f');'
        f'out tags;'
    )
    for attempt in range(3):
        try:
            if attempt > 0:
                wait = 15 * attempt
                log(f"Overpass retry {attempt}/2 — waiting {wait}s", log_path)
                time.sleep(wait)
            log(f"Overpass query (attempt {attempt + 1})", log_path)
            resp = requests.get(OVERPASS_URL, params={"data": query}, timeout=90)
            if resp.status_code == 200:
                return resp.json().get("elements", [])
            log(f"Overpass HTTP {resp.status_code} on attempt {attempt + 1}", log_path)
        except Exception as exc:
            log(f"Overpass attempt {attempt + 1} failed: {exc}", log_path)
    log("Overpass failed after 3 attempts", log_path)
    return []


def _parse_location(query):
    """Extract city and state from a query string like 'lawyers in New York City, New York'."""
    match = re.search(r'\bin\s+(.+)$', query, re.IGNORECASE)
    location = match.group(1).strip() if match else query.strip()
    parts = [p.strip() for p in location.split(",")]
    city  = parts[0] if parts else location
    state = parts[1].strip() if len(parts) > 1 else ""
    return city, state


class FirmParser:

    def __init__(self, log_path=None):
        self.log_path = log_path

    def scrape_firms(self, query, target=50, status_callback=None, cancel_event=None):
        """Discover law firms via OpenStreetMap (Nominatim + Overpass). Free, no API key needed."""
        if cancel_event and cancel_event.is_set():
            return []

        city, state = _parse_location(query)
        log(f"Firm discovery — city: '{city}', state: '{state}'", self.log_path)

        # Step 1: geocode city → bounding box
        bbox = _geocode_city(city, state, self.log_path)
        if not bbox:
            log("Could not geocode city — no firms returned", self.log_path)
            return []

        south, north, west, east = bbox

        # Step 2: fetch all lawyer nodes in bounding box
        elements = _overpass_lawyers(south, north, west, east, self.log_path)
        log(f"Overpass returned {len(elements)} lawyer entries", self.log_path)

        # Step 3: extract firms that have a website
        results = []
        seen_websites = set()

        for el in elements:
            if cancel_event and cancel_event.is_set():
                break
            if len(results) >= target:
                break

            tags = el.get("tags", {})
            name = tags.get("name", "").strip()
            website = (
                tags.get("website", "")
                or tags.get("contact:website", "")
                or tags.get("url", "")
            ).strip()

            if not name or not website:
                continue

            # Normalise to root domain
            try:
                parsed = urlparse(website if "://" in website else "https://" + website)
                website = f"{parsed.scheme}://{parsed.netloc}"
            except Exception:
                continue

            if website in seen_websites:
                continue

            seen_websites.add(website)
            results.append({"Firm Name": name, "Website": website})

            if status_callback:
                status_callback(len(results), target)
            log(f"{len(results)}. {name} — {website}", self.log_path)

        log(f"Firm discovery done: {len(results)} firms with websites", self.log_path)
        return results

    # Backward-compatible alias
    def scrape_google_places(self, query, target=50, status_callback=None, cancel_event=None):
        return self.scrape_firms(query, target, status_callback, cancel_event)


if __name__ == "__main__":
    import pandas as pd
    query = "medical malpractice and personal injury lawyers in New York City, New York"
    data = FirmParser().scrape_firms(query, 50)
    print(f"\nFound {len(data)} firms")
    if data:
        pd.DataFrame(data).to_csv("osm_firms.csv", index=False)
        print("Saved to osm_firms.csv")
