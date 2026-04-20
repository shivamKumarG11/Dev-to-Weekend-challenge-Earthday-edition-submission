"""
app.py — TERRA-STATE: VOX ATLAS v2.0
FastAPI Application Entry Point

Routes:
  POST /tick           → Advance simulation, returns WorldTickResponse
  GET  /world          → Current WorldState (no tick advance, for hydration)
  GET  /oracle/{x}/{y} → Detailed cell data for Oracle View modal
  POST /agent-request  → Authenticated spatial agent action
  GET  /analyze        → Gemini Planetary Advisor bulletin
  GET  /config         → Backboard climate multipliers
  POST /reset          → Reinitialise world to initial conditions
"""
from __future__ import annotations
import asyncio
import logging
import os
from typing import Optional

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core.engine import world_engine
from core.models import (
    AgentRequest, AgentResponse,
    AnalystReport, ConfigMultipliers,
    OracleData, WorldCell, WorldState,
    WorldTickResponse, SimulationEvent,
)
from core import simulation as _sim
from services.auth0_guard import verify_agent_token
from services.gemini_oracle import gemini_analyze
from services.snowflake_log import sf_log_tick
from services.backboard import backboard_service

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | terra-state | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("terra-state")

app = FastAPI(
    title="TERRA-STATE: VOX ATLAS",
    description=(
        "Gamified Macro-Economic Ecological Simulation — "
        "200×600 Spatial World Matrix with real-time visual feedback loops."
    ),
    version="2.0.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Request Schemas ──────────────────────────────────────────────────────────

class TickRequest(BaseModel):
    drought_severity_index: Optional[float] = None
    global_market_demand:   Optional[float] = None


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.post("/tick", response_model=WorldTickResponse, tags=["Simulation"])
async def post_tick(body: TickRequest = TickRequest()) -> WorldTickResponse:
    """
    Advance the simulation by one tick.
    Optionally override Backboard multipliers with body values.
    Fires an async Snowflake log task (non-blocking).
    """
    config = await backboard_service.get_multipliers(
        drought_override=body.drought_severity_index,
        demand_override=body.global_market_demand,
    )
    world_state, events = world_engine.tick(
        drought_severity_index=config.drought_severity_index,
        global_market_demand=config.global_market_demand,
    )
    # Attach events to the state object (for unified access)
    world_state.events = events
    # Fire-and-forget Snowflake logging
    asyncio.create_task(sf_log_tick(world_state.tick_id, world_state))

    return WorldTickResponse(
        status="success",
        tick_id=world_state.tick_id,
        world_state=world_state,
        global_metrics=world_state.global_metrics,
        events=events,
    )


@app.get("/world", tags=["Simulation"])
async def get_world() -> dict:
    """
    Return current WorldState without advancing tick.
    Used by the frontend on page load for grid hydration.
    """
    ws = world_engine.get_world()
    return {
        "tick_id":        ws.tick_id,
        "world_state":    ws.model_dump(),
        "global_metrics": ws.global_metrics.model_dump(),
    }


@app.get("/oracle/{x}/{y}", response_model=OracleData, tags=["Simulation"])
async def get_oracle(x: int, y: int) -> OracleData:
    """
    Return detailed data for the Oracle View modal when a tile is clicked.
    Includes neighbor breakdown, active pressures, and predicted soil death.
    """
    if not (0 <= x <= 199 and 0 <= y <= 599):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Coordinates out of range: x={x}, y={y}. Must be in x[0, 199], y[0, 599].",
        )

    cell_dict = world_engine.get_cell(x, y)
    neighbors = world_engine.get_neighbors_of(x, y)

    agri_n  = sum(1 for n in neighbors if n['type'].value == "Agriculture")
    forest_n= sum(1 for n in neighbors if n['type'].value == "Forest")
    water_n = sum(1 for n in neighbors if n['type'].value == "Water")
    urban_n = sum(1 for n in neighbors if n['type'].value == "Urban")
    bare_n  = sum(1 for n in neighbors if n['type'].value == "BareSoil")

    # Predicted soil death
    soil_death: Optional[float] = None
    pressures:  list[str]       = []

    ct_val = cell_dict['type'].value
    health = cell_dict['health']

    if ct_val == "Agriculture":
        soil_death = round(health / _sim.AGRI_SOIL_DEPLETION, 1)
        if health < 20:
            pressures.append("⚠ CRITICAL — soil collapse imminent")
        elif health < 40:
            pressures.append("⚠ WARNING — depletion accelerating")
        if agri_n >= 4:
            pressures.append("High agriculture density in surrounding sectors")

    elif ct_val == "Forest":
        if agri_n >= 2:
            pressures.append(f"Deforestation pressure: {agri_n} adjacent Agriculture sectors")
        if urban_n >= 1:
            pressures.append("Urban expansion risk — adjacent Urban development")

    elif ct_val == "Water":
        if "ERODED" in cell_dict.get('effects', []):
            pressures.append("ERODED — soil health cascade corrupting watershed")
        if agri_n >= 2:
            pressures.append("Agricultural runoff detected in watershed corridor")

    elif ct_val == "Urban":
        stage = cell_dict.get('evolution_stage', 1)
        stage_names = {1: "Hut Cluster", 2: "Mid-rise District", 3: "Tower Complex"}
        pressures.append(f"Stage {stage}: {stage_names.get(stage, 'Unknown')}")

    elif ct_val == "BareSoil":
        if "DESICCATED" in cell_dict.get('effects', []):
            pressures.append("DESICCATED — hydrological collapse event victim")

    return OracleData(
        cell=WorldCell(**cell_dict),
        agriculture_neighbors=agri_n,
        forest_neighbors=forest_n,
        water_neighbors=water_n,
        urban_neighbors=urban_n,
        bare_soil_neighbors=bare_n,
        predicted_soil_death_in_ticks=soil_death,
        active_pressures=pressures,
    )


@app.post("/agent-request", response_model=AgentResponse, tags=["Agents"])
async def post_agent_request(
    body:        AgentRequest,
    jwt_payload: dict = Depends(verify_agent_token),
) -> AgentResponse:
    """
    Apply an authenticated spatial agent action to a specific grid cell.
    JWT sub must match agent_id (unless dev-token mock mode).
    """
    is_mock = jwt_payload.get("mock", False)
    jwt_sub = jwt_payload.get("sub", "")
    if not is_mock and body.agent_id != jwt_sub:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"agent_id '{body.agent_id}' does not match token sub '{jwt_sub}'.",
        )

    try:
        updated_cell_dict = world_engine.apply_agent_action(body.x, body.y, body.action)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    cell_after = WorldCell(**updated_cell_dict)

    log.info(
        f"Agent '{body.agent_id}' applied '{body.action}' at "
        f"[{body.x},{body.y}] → {cell_after.type} (health={cell_after.health})"
    )
    return AgentResponse(
        status="applied",
        agent_id=body.agent_id,
        action=body.action,
        x=body.x,
        y=body.y,
        cell_after=cell_after,
    )


@app.get("/analyze", response_model=AnalystReport, tags=["Intelligence"])
async def get_analyze() -> AnalystReport:
    """Request a Gemini Planetary Advisor analysis of the current world state."""
    world_state = world_engine.get_world()
    if world_state.tick_id == 0:
        return AnalystReport(
            analyst_report=(
                "[ADVISORY SYSTEM ONLINE]: Satellite feed initialised. "
                "Awaiting first tick data before issuing assessment."
            )
        )
    report = await gemini_analyze(world_state)
    return AnalystReport(analyst_report=report)


@app.get("/config", response_model=ConfigMultipliers, tags=["Configuration"])
async def get_config() -> ConfigMultipliers:
    """Return current Backboard climate multipliers."""
    return await backboard_service.get_multipliers()


@app.post("/reset", tags=["Simulation"])
async def post_reset() -> dict:
    """Reset the World Matrix to initial conditions."""
    ws = world_engine.reset()
    log.info("World Matrix reset to initial conditions.")
    return {"status": "reset", "tick_id": ws.tick_id, "world_state": ws.model_dump(), "global_metrics": ws.global_metrics.model_dump()}


# ─── Static Files & SPA ───────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
async def serve_spa() -> FileResponse:
    return FileResponse("static/index.html")


app.mount("/static", StaticFiles(directory="static"), name="static_files")


# ─── Error Handler ────────────────────────────────────────────────────────────

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": f"HTTP {exc.status_code}", "detail": exc.detail},
    )


# ─── Startup Log ──────────────────────────────────────────────────────────────

@app.on_event("startup")
async def on_startup() -> None:
    ws = world_engine.get_world()
    m  = ws.global_metrics
    log.info("═" * 64)
    log.info("  TERRA-STATE: VOX ATLAS  v2.0 — Spatial Ecological Engine")
    log.info("═" * 64)
    log.info(f"  World Matrix   : 200×600 grid | 120,000 cells")
    log.info(f"  Initial state  : W={m.W:.1f} S={m.S:.1f} F={m.F:.1f} A={m.A:.1f} E={m.E:.1f}")
    log.info(f"  Auth0          : {'configured' if os.getenv('AUTH0_DOMAIN') else '⚠ mock (dev-token)'}")
    log.info(f"  Gemini         : {'configured' if os.getenv('GEMINI_API_KEY') else '⚠ mock mode'}")
    log.info(f"  Snowflake      : {'configured' if os.getenv('SNOWFLAKE_ACCOUNT') else '⚠ mock (console)'}")
    log.info(f"  Backboard      : {'configured' if os.getenv('BACKBOARD_API_KEY') else '⚠ mock (neutral)'}")
    log.info(f"  SPA            : http://localhost:8000/")
    log.info(f"  API Docs       : http://localhost:8000/api/docs")
    log.info("═" * 64)

