"""Completed-attempt timing events; no averaging or timer-driven operations."""
from dataclasses import dataclass


@dataclass(frozen=True)
class PasswordAttemptElapsed:
    seconds: float
