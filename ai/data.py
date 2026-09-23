"""Load unmodified Career Quest JSON/CSV files, including extra evaluation data."""

import csv
import json
from pathlib import Path


def load_dataset(directory):
    directory = Path(directory)
    def read(name):
        with (directory / name).open(encoding="utf-8-sig") as stream:
            return json.load(stream)
    employees = read("employees.json")
    events = read("events.json")
    skills = read("skills.json")
    if len({x["meta"]["as_of_date"] for x in (employees, events, skills)}) != 1:
        raise ValueError("Dataset snapshot dates disagree")
    with (directory / "activity_history.csv").open(encoding="utf-8-sig", newline="") as stream:
        history = list(csv.DictReader(stream))
    return employees, events, skills, history
