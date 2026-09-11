"""Quarterly infrastructure report helper."""

import json


class RegionReport:
    """Summarizes capacity for a single region."""

    def __init__(self, name: str, nodes: int, utilization: float) -> None:
        self.name = name
        self.nodes = nodes
        self.utilization = utilization

    def is_healthy(self) -> bool:
        return self.utilization < 0.85


def load_report(path: str) -> list[RegionReport]:
    with open(path) as f:
        data = json.load(f)

    return [
        RegionReport(entry["region"], entry["nodes"], entry["utilization"])
        for entry in data
    ]
