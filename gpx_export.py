#!/usr/bin/env python3
"""Produit un GPX public léger à intervalle kilométrique régulier."""

import math
import os

import gpxpy
import gpxpy.gpx
from gpxpy.geo import simplify_polyline


def distance_km(a, b):
    lat1, lon1 = map(math.radians, (a.latitude, a.longitude))
    lat2, lon2 = map(math.radians, (b.latitude, b.longitude))
    value = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2)
        * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * 6371.0088 * math.asin(math.sqrt(value))


def read_points(path):
    with open(path, "r", encoding="utf-8") as handle:
        source = gpxpy.parse(handle)
    points = [
        point for track in source.tracks
        for segment in track.segments for point in segment.points
    ]
    if not points:
        points = [point for route in source.routes for point in route.points]
    if len(points) < 2:
        raise ValueError(f"Trace GPX vide ou trop courte : {path}")
    return points


def clean_point(point):
    """Copie les données utiles sans les extensions propriétaires fragiles."""
    return gpxpy.gpx.GPXTrackPoint(
        point.latitude, point.longitude,
        elevation=point.elevation, time=point.time,
    )


def simplify_points(points, interval_km):
    """Vise un point par intervalle en préservant en priorité les virages."""
    total = sum(distance_km(a, b) for a, b in zip(points, points[1:]))
    target_count = max(2, round(total / interval_km) + 1)
    low, high = 0.0, 1000.0
    best = points
    for _ in range(22):
        tolerance = (low + high) / 2
        candidate = simplify_polyline(points, tolerance)
        if abs(len(candidate) - target_count) < abs(len(best) - target_count):
            best = candidate
        if len(candidate) > target_count:
            low = tolerance
        else:
            high = tolerance
    return [clean_point(point) for point in best], total


def export_simplified_gpx(source_path, destination_path, interval_km=1, name=None):
    """Écrit le GPX public ; ne réécrit pas un fichier déjà utilisé comme source."""
    if os.path.abspath(source_path) == os.path.abspath(destination_path):
        return destination_path
    points, total = simplify_points(read_points(source_path), interval_km)
    document = gpxpy.gpx.GPX()
    track = gpxpy.gpx.GPXTrack(name=name or "Trace simplifiée")
    track.segments.append(gpxpy.gpx.GPXTrackSegment(points=points))
    document.tracks.append(track)
    os.makedirs(os.path.dirname(destination_path), exist_ok=True)
    with open(destination_path, "w", encoding="utf-8") as handle:
        handle.write(document.to_xml())
    print(
        f"GPX public : {destination_path} "
        f"({len(points)} points pour {total:.1f} km)"
    )
    return destination_path
