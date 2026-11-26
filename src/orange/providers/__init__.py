"""Provider registry and env-driven selection (live MCP when a token exists, else fixtures)."""

from __future__ import annotations

import os
from pathlib import Path

from ..mcpl import McpHttpClient
from ..models import QueryContext
from .district import DistrictProvider
from .swiggy_dineout import SwiggyDineoutProvider

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"


def build_providers(ctx: QueryContext, *, fixtures_dir: str | Path | None = None, live: bool = True):
    """Return (providers, notes).

    Live mode activates per provider when its token env var is present; otherwise the
    schema-accurate fixture is used and the caller is told which mode ran.
    """
    root = Path(fixtures_dir) if fixtures_dir else FIXTURES
    providers = []
    notes: list[str] = []

    swiggy_token = os.environ.get("SWIGGY_MCP_TOKEN")
    if live and swiggy_token:
        client = McpHttpClient(SwiggyDineoutProvider.server, token=swiggy_token)
        client.initialize()
        providers.append(SwiggyDineoutProvider(ctx, client=client))
        notes.append("swiggy_dineout: LIVE (mcp.swiggy.com/dineout)")
    else:
        providers.append(SwiggyDineoutProvider(ctx, fixture=root / "swiggy_dineout_koramangala.json"))
        notes.append("swiggy_dineout: fixture (set SWIGGY_MCP_TOKEN for live)")

    district_endpoint = os.environ.get("DISTRICT_MCP_ENDPOINT")
    district_token = os.environ.get("DISTRICT_MCP_TOKEN")
    if live and district_endpoint and district_token:
        client = McpHttpClient(district_endpoint, token=district_token)
        client.initialize()
        providers.append(DistrictProvider(ctx, client=client))
        notes.append(f"district: LIVE ({district_endpoint})")
    else:
        providers.append(DistrictProvider(ctx, fixture=root / "district_koramangala.json"))
        notes.append("district: fixture (set DISTRICT_MCP_ENDPOINT + DISTRICT_MCP_TOKEN for live)")

    return providers, notes
