"""Explicit simulated and OpenFOAM execution interfaces."""

from .execution import run_case
from .physics import force_coefficient_evidence, read_force_coefficients

__all__ = ["run_case", "force_coefficient_evidence", "read_force_coefficients"]
