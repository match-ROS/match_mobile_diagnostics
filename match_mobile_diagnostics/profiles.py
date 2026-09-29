"""Versioned expectations, deliberately independent of inspected host config."""
from importlib.resources import files
import json

ROBOTS = ("mur620a", "mur620b", "mur620c", "mur620d")


def load_profile(robot):
    if robot not in ROBOTS:
        raise ValueError(f"Unbekannter Roboter: {robot}")
    return json.loads(files("match_mobile_diagnostics").joinpath("profiles", robot + ".json").read_text())
