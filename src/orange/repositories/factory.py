"""Repository factory: live MCP when a token exists, fixture repositories otherwise."""

from __future__ import annotations

import os
from pathlib import Path

from ..domain.models import QueryContext
from .district import DistrictRepository
from .mcp import McpHttpClient
from .swiggy import SwiggyDineoutRepository

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"


def build_repositories(ctx: QueryContext, *, fixtures_dir: str | Path | None = None, live: bool = True):
    """Return (repositories, notes).

    Live mode activates per provider when its token env var is present; otherwise the
    schema-accurate fixture is used and the caller is told which mode ran.
    """
    root = Path(fixtures_dir) if fixtures_dir else FIXTURES
    repositories = []
    notes: list[str] = []

    swiggy_token = os.environ.get("SWIGGY_MCP_TOKEN")
    if live and swiggy_token:
        client = McpHttpClient(SwiggyDineoutRepository.server, token=swiggy_token)
        client.initialize()
        repositories.append(SwiggyDineoutRepository(ctx, client=client))
        notes.append("swiggy_dineout: LIVE (mcp.swiggy.com/dineout)")
    else:
        repositories.append(SwiggyDineoutRepository(ctx, fixture=root / "swiggy_dineout_koramangala.json"))
        notes.append("swiggy_dineout: fixture (set SWIGGY_MCP_TOKEN for live)")

    district_endpoint = os.environ.get("DISTRICT_MCP_ENDPOINT")
    district_token = os.environ.get("DISTRICT_MCP_TOKEN")
    if live and district_endpoint and district_token:
        client = McpHttpClient(district_endpoint, token=district_token)
        client.initialize()
        repositories.append(DistrictRepository(ctx, client=client))
        notes.append(f"district: LIVE ({district_endpoint})")
    else:
        repositories.append(DistrictRepository(ctx, fixture=root / "district_koramangala.json"))
        notes.append("district: fixture (set DISTRICT_MCP_ENDPOINT + DISTRICT_MCP_TOKEN for live)")

    return repositories, notes


# Backwards-compatible name: these objects are provider repositories.
build_providers = build_repositories
