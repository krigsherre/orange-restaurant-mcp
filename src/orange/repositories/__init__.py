"""Data access layer: MCP-backed restaurant repositories + fixture fallback."""

from .base import Provider, ReservationRepository, RestaurantRepository
from .factory import build_providers, build_repositories

__all__ = ["Provider", "RestaurantRepository", "ReservationRepository", "build_repositories", "build_providers"]
