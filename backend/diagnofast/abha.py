"""Past health records from ABHA.

Real ABHA access needs ABDM sandbox approval, an exit review and a security
audit. Until then SimulatedAbha returns made-up records, and every report that
uses them says so. A real client only has to provide the same fetch() method.
"""
from dataclasses import dataclass, field
from typing import List, Protocol


@dataclass
class HealthRecords:
    conditions: List[str] = field(default_factory=list)
    prescriptions: List[str] = field(default_factory=list)
    lab_reports: List[str] = field(default_factory=list)
    is_demo_data: bool = True


class AbhaClient(Protocol):
    def fetch(self, abha_id: str) -> HealthRecords: ...


class SimulatedAbha:
    def fetch(self, abha_id: str) -> HealthRecords:
        return HealthRecords(
            conditions=["Type 2 diabetes (2021)", "High blood pressure (2023)"],
            prescriptions=["Metformin 500 mg, twice a day (2025)", "Amlodipine 5 mg, once a day (2025)"],
            lab_reports=["HbA1c 7.2% (March 2026)", "Blood pressure 138/86 (March 2026)"],
            is_demo_data=True,
        )


def get_abha_client() -> AbhaClient:
    return SimulatedAbha()
