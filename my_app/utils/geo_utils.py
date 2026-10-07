"""Geographic calculations and presentation-safe OSM name normalization."""
from __future__ import annotations

import math
import re


EARTH_RADIUS_KM = 6371.0


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the great-circle distance between two WGS84 coordinates in km."""
    lat1_rad, lon1_rad, lat2_rad, lon2_rad = map(math.radians, (lat1, lon1, lat2, lon2))
    latitude_delta = lat2_rad - lat1_rad
    longitude_delta = lon2_rad - lon1_rad
    a = (math.sin(latitude_delta / 2) ** 2 + math.cos(lat1_rad) * math.cos(lat2_rad)
         * math.sin(longitude_delta / 2) ** 2)
    return EARTH_RADIUS_KM * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


_NOISY_SUFFIX = re.compile(
    r"(?:\s*[-,–—:]?\s*)(?:astro\s+turf|synthetic\s+turf(?:\s+ground)?|"
    r"football\s+turf|cricket\s+ground|football\s+ground|sports?\s+ground|"
    r"playground)\s*$",
    re.IGNORECASE,
)


def clean_google_maps_name(raw_name: str) -> str:
    """Remove generic OSM venue suffixes while retaining the venue's identity."""
    name = re.sub(r"\s+", " ", (raw_name or "").strip())
    if not name:
        return "Unnamed Sports Ground"
    cleaned = name
    while True:
        without_suffix = _NOISY_SUFFIX.sub("", cleaned).strip(" -,:–—")
        if without_suffix == cleaned:
            break
        cleaned = without_suffix
    return cleaned or name
