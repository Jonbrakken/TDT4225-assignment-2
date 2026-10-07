import math

GPS_INTERVAL_SECONDS = 15
MAX_SPEED_KMH = 200


def segment_speed_kmh(first, second):
    """Great-circle speed between [longitude, latitude] samples 15 seconds apart."""
    lon1, lat1 = map(math.radians, first)
    lon2, lat2 = map(math.radians, second)
    a = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    distance_km = 6371.0088 * 2 * math.asin(math.sqrt(min(1.0, max(0.0, a))))
    return distance_km * 3600 / GPS_INTERVAL_SECONDS


def excessive_speed(points):
    """Return the first offending segment (1-based) and speed, or None."""
    for segment, (first, second) in enumerate(zip(points, points[1:]), start=1):
        speed = segment_speed_kmh(first, second)
        if speed > MAX_SPEED_KMH:
            return segment, speed
    return None
