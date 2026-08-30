"""Content sources. Each returns a list of Candidate objects."""

from .base import Candidate, harvest_all

__all__ = ["Candidate", "harvest_all"]
