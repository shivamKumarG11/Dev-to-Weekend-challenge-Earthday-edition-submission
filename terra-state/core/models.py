"""
core/models.py — TERRA-STATE: VOX ATLAS v2.0
Pydantic schemas for the spatial World Matrix engine.
"""
from __future__ import annotations
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class CellType(str, Enum):
    Water       = "Water"
    Forest      = "Forest"
    Agriculture = "Agriculture"
    Urban       = "Urban"
    BareSoil    = "BareSoil"
    Mountain    = "Mountain"


class WorldCell(BaseModel):
    x: int               = Field(ge=0, le=199)
    y: int               = Field(ge=0, le=599)
    type: CellType
    health: float        = Field(default=80.0, ge=0.0, le=100.0)
    evolution_stage: int = Field(default=1, ge=1, le=3)   # Urban only
    effects: list[str]   = Field(default_factory=list)


class GlobalMetrics(BaseModel):
    W: float = Field(description="Avg Water cell health [0-100]")
    S: float = Field(description="Avg Agriculture cell health — soil proxy")
    F: float = Field(description="Forest cells as % of total grid area")
    A: float = Field(description="Agriculture cells as % of total grid area")
    E: float = Field(description="Economy index from Urban cell health [0-100]")
    C: float = Field(description="Atmospheric Carbon PPM (base 250, critical 400+)")


class SimulationEvent(BaseModel):
    type: str
    x: Optional[int]    = None
    y: Optional[int]    = None
    description: str


class WeatherBlock(BaseModel):
    """
    One weather block covering a 10×10 cell area.
    Grid is 20 cols × 60 rows = 1,200 blocks total.
    """
    moisture:     float = Field(default=0.0,  ge=0.0, le=100.0,
                                description="Atmospheric moisture [0-100]")
    cloud_cover:  float = Field(default=0.0,  ge=0.0, le=1.0,
                                description="Fractional cloud cover [0-1]")
    precipitation: float = Field(default=0.0, ge=0.0, le=1.0,
                                 description="Precipitation intensity [0-1]")


class WorldState(BaseModel):
    tick_id: int
    grid: list[list[WorldCell]]   # grid[row][col], shape (600, 200)
    global_metrics: GlobalMetrics
    events: list[SimulationEvent]  = Field(default_factory=list)
    # Coarse 20×60 weather overlay (row-major: weather_grid[wr][wc])
    weather_grid: list[list[WeatherBlock]] = Field(default_factory=list)



class WorldTickResponse(BaseModel):
    status: str                    = "success"
    tick_id: int
    world_state: WorldState
    global_metrics: GlobalMetrics
    events: list[SimulationEvent]


class OracleData(BaseModel):
    cell: WorldCell
    agriculture_neighbors: int     = 0
    forest_neighbors: int          = 0
    water_neighbors: int           = 0
    urban_neighbors: int           = 0
    bare_soil_neighbors: int       = 0
    predicted_soil_death_in_ticks: Optional[float] = None
    active_pressures: list[str]    = Field(default_factory=list)


class AgentAction(str, Enum):
    reforest = "reforest"   # BareSoil → Forest at (x, y)
    clear    = "clear"      # Any cell → BareSoil at (x, y)
    develop  = "develop"    # Forest  → Urban  at (x, y)


class AgentRequest(BaseModel):
    agent_id: str
    action:   AgentAction
    x:        int = Field(ge=0, le=199)
    y:        int = Field(ge=0, le=599)


class AgentResponse(BaseModel):
    status:     str = "applied"
    agent_id:   str
    action:     str
    x:          int
    y:          int
    cell_after: WorldCell


class AnalystReport(BaseModel):
    analyst_report: str


class ConfigMultipliers(BaseModel):
    drought_severity_index: float = Field(default=1.0, ge=0.0, le=5.0)
    global_market_demand:   float = Field(default=1.0, ge=0.0, le=5.0)
