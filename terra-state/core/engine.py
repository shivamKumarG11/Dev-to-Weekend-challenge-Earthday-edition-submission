"""
core/engine.py — TERRA-STATE: VOX ATLAS v2.0
World Matrix State Machine — the single source of truth.

Holds the 200x600 grid in memory behind a threading.Lock.
All external mutations route through this module's public API.
Physics are delegated to simulation.py (pure functions).
"""
from __future__ import annotations
import copy
import math
import random
import threading
from collections import deque

from core.models import (
    CellType, WorldCell, WorldState, GlobalMetrics, SimulationEvent, WeatherBlock,
)
from core import simulation
from core.simulation import tick_weather, _init_weather_grid

GRID_WIDTH  = 200
GRID_HEIGHT = 600
MAX_HISTORY = 100

# Urban starts at 60 so the three E-milestones (60, 75, 90) fire progressively
_INITIAL_HEALTH: dict[CellType, float] = {
    CellType.Water:       80.0,
    CellType.Forest:      80.0,
    CellType.Agriculture: 80.0,
    CellType.Urban:       60.0,   # intentionally not 80 — milestones must earn growth
    CellType.BareSoil:    40.0,
    CellType.Mountain:    100.0,
}


def _build_initial_grid() -> list[list[dict]]:
    """Construct a massive 200x600 starting grid with U-shaped mountains and a river.

    Farmland placement strategy
    ───────────────────────────
    Instead of a flat random probability, we:
      1. Seed Agriculture anchors in a ring just outside each city radius.
      2. BFS-flood each anchor outward to grow a large contiguous patch
         (target size: proportional to city size, ~40–120 cells per patch).
      3. A small residual probability (3 %) places isolated rural fields
         everywhere else so the countryside isn't entirely blank.
    This produces realistic belts of farmland wrapping around urban cores.
    """
    # ── Step 1: decide cell types for every city centre ──────────────────────
    city_centers: list[tuple[int, int, float]] = []
    for _ in range(15):
        cx = random.randint(20, GRID_WIDTH - 20)
        cy = random.randint(60, 520)
        cr = random.uniform(3, 8)
        city_centers.append((cx, cy, cr))

    # ── Step 2: multiple river tributaries from mountains ───────────────────
    river_path: set[tuple[int, int]] = set()
    num_springs = random.randint(3, 6)
    for _ in range(num_springs):
        rx = random.uniform(20.0, GRID_WIDTH - 20.0)
        # Flow from mountain base (y=40) towards ocean boundary (y=548)
        for ry in range(35, 548):
            irx = int(rx)
            # Tributaries start narrow (1 cell)
            if 0 <= irx < GRID_WIDTH:
                river_path.add((irx, ry))
            
            # Meander logic: sin wave + random walk
            # Lower frequency sin (0.03) for long sweeping curves
            rx += math.sin(ry * 0.03) * 0.7 + random.uniform(-0.8, 0.8)
            rx = max(5.0, min(GRID_WIDTH - 5.0, rx))

    # ── Step 3: build base grid (Urban / Mountain / Water / Forest / BareSoil) ──
    # Agriculture will be stamped in a second pass so the BFS has a settled grid.
    grid: list[list[dict]] = []
    for y in range(GRID_HEIGHT):
        row: list[dict] = []
        for x in range(GRID_WIDTH):
            effects = []
            if y < 40:
                ct = CellType.Mountain
                # High altitude glaciers provide water source for the rivers
                if y < 15 and random.random() < 0.4:
                    effects.append("GLACIER")
            elif y > 550:
                ct = CellType.Water
            elif (x, y) in river_path:
                ct = CellType.Water
            else:
                in_city = any(
                    math.hypot(x - cx, y - cy) < cr
                    for cx, cy, cr in city_centers
                )
                if in_city:
                    ct = CellType.Urban
                elif random.random() < 0.02:
                    ct = CellType.BareSoil
                else:
                    ct = CellType.Forest

            row.append({
                'x':               x,
                'y':               y,
                'type':            ct,
                'health':          _INITIAL_HEALTH[ct],
                'evolution_stage': 1,
                'effects':         effects,
            })
        grid.append(row)

    # ── Step 4: BFS-flood farmland patches around each city ──────────────────
    def _is_playable(x: int, y: int) -> bool:
        """Cell exists and is not Mountain, Water, or Urban."""
        if x < 0 or x >= GRID_WIDTH or y < 0 or y >= GRID_HEIGHT:
            return False
        t = grid[y][x]['type']
        return t not in (CellType.Mountain, CellType.Water, CellType.Urban)

    agri_cells: set[tuple[int, int]] = set()  # guard against double-painting

    for cx, cy, cr in city_centers:
        # How big should the agricultural belt be?  Scale with city radius.
        target_patch_size = int(random.uniform(60, 140) * (cr / 5.5))

        # Seed ring: a ring of candidate anchor points just outside the city.
        ring_r_min = cr + 1
        ring_r_max = cr + 5
        seeds: list[tuple[int, int]] = []
        for dx in range(-int(ring_r_max) - 2, int(ring_r_max) + 3):
            for dy in range(-int(ring_r_max) - 2, int(ring_r_max) + 3):
                dist = math.hypot(dx, dy)
                if ring_r_min <= dist <= ring_r_max:
                    sx, sy = cx + dx, cy + dy
                    if _is_playable(sx, sy) and (sx, sy) not in agri_cells:
                        seeds.append((sx, sy))

        if not seeds:
            continue  # city too close to boundary — skip

        # Shuffle seeds so growth direction is randomised each city.
        random.shuffle(seeds)

        # BFS flood-fill outward from the ring.
        frontier: deque[tuple[int, int]] = deque()
        visited:  set[tuple[int, int]]   = set()
        for s in seeds[:max(1, len(seeds) // 3)]:   # start from a subset of seeds
            frontier.append(s)
            visited.add(s)

        painted = 0
        while frontier and painted < target_patch_size:
            fx, fy = frontier.popleft()
            if (fx, fy) not in agri_cells and _is_playable(fx, fy):
                grid[fy][fx]['type']   = CellType.Agriculture
                grid[fy][fx]['health'] = _INITIAL_HEALTH[CellType.Agriculture]
                agri_cells.add((fx, fy))
                painted += 1

            # Expand to 4-connected neighbours with small random spread chance
            neighbours = [(fx-1, fy), (fx+1, fy), (fx, fy-1), (fx, fy+1)]
            random.shuffle(neighbours)
            for nx, ny in neighbours:
                if (nx, ny) not in visited and _is_playable(nx, ny):
                    # Keep farm closer to the city than very far away
                    if math.hypot(nx - cx, ny - cy) < cr + 28:
                        visited.add((nx, ny))
                        frontier.append((nx, ny))

    # ── Step 5: residual scattered farmland (city-proximity only) ────────────
    # Small isolated rural parcels are possible, but ONLY near a city.
    # Deep-forest cells (far from every city) must never receive farmland —
    # wilderness areas represent pristine ecosystems untouched by agriculture.
    FARM_MAX_CITY_DIST = 32   # cells — beyond this radius a Forest is "deep wilderness"
    for y in range(GRID_HEIGHT):
        for x in range(GRID_WIDTH):
            if grid[y][x]['type'] != CellType.Forest or (x, y) in agri_cells:
                continue
            # Only allow scatter if this cell is close enough to some city
            near_city = any(
                math.hypot(x - cx, y - cy) <= FARM_MAX_CITY_DIST
                for cx, cy, cr in city_centers
            )
            if near_city and random.random() < 0.03:
                grid[y][x]['type']   = CellType.Agriculture
                grid[y][x]['health'] = _INITIAL_HEALTH[CellType.Agriculture]

    return grid


# ─── Engine Class ─────────────────────────────────────────────────────────────

class WorldEngine:
    """
    Thread-safe state machine for the TERRA-STATE spatial simulation.
    Single global instance (singleton) exported at module level.
    """

    def __init__(self) -> None:
        self._lock                   = threading.Lock()
        self._grid: list[list[dict]] = _build_initial_grid()
        self._tick_counter: int      = 0
        self._tick_history: deque    = deque(maxlen=MAX_HISTORY)
        self._weather_grid: list[list[dict]] = _init_weather_grid()
        # Track which E-milestones have triggered Urban expansion
        self._urban_milestones_fired: dict[int, bool] = {
            60: False, 75: False, 90: False,
        }

    # ── Public API ─────────────────────────────────────────────────────────────

    def tick(
        self,
        drought_severity_index: float = 1.0,
        global_market_demand:   float = 1.0,
    ) -> tuple[WorldState, list[SimulationEvent]]:
        """
        Advance the simulation by one time-step.

        Execution order (critical — each step feeds the next):
          1. Compute metrics on current grid.
          2. apply_physics   → physics on all cells (no in-place mutation of source).
          3. Re-compute metrics on post-physics grid.
          4. check_deforestation → may convert Forest→Agriculture.
          5. Re-compute metrics (agri count changed).
          6. check_urban_expansion → may convert Forest→Urban.
          7. Re-compute metrics.
          8. check_desertification → catastrophic cascade if W==0.
          9. Commit, increment tick counter, build and return WorldState.

        Returns (WorldState, all_events_this_tick). Thread-safe.
        """
        with self._lock:
            # Step 1
            metrics = simulation.compute_metrics(self._grid)

            # Step 2 — pure-function physics (reads src, writes new_grid)
            new_grid, events = simulation.apply_physics(
                self._grid,
                drought_index=drought_severity_index,
                demand_multiplier=global_market_demand,
                metrics=metrics,
                weather_grid=self._weather_grid,
            )

            # Step 2b — advance weather (reads post-physics biome grid for evaporation)
            self._weather_grid = tick_weather(
                self._weather_grid,
                new_grid,
                drought_index=drought_severity_index,
            )

            # Step 3 — metrics after physics
            metrics = simulation.compute_metrics(new_grid)

            # Step 4 — deforestation
            new_grid, defor_events = simulation.check_deforestation(new_grid, metrics)
            events.extend(defor_events)

            # Step 5
            metrics = simulation.compute_metrics(new_grid)

            # Step 6 — urban expansion
            new_grid, urban_events, self._urban_milestones_fired = \
                simulation.check_urban_expansion(new_grid, metrics, self._urban_milestones_fired)
            events.extend(urban_events)

            # Step 7
            metrics = simulation.compute_metrics(new_grid)

            # Step 8 — desertification cascade
            new_grid, desert_events = simulation.check_desertification(new_grid, metrics)
            events.extend(desert_events)

            # Step 9 — commit
            self._grid = new_grid
            self._tick_counter += 1
            final_metrics = simulation.compute_metrics(self._grid)
            world_state   = self._build_world_state(final_metrics, events)
            self._tick_history.append({
                'tick_id': self._tick_counter,
                'metrics': final_metrics,
                'events':  [e.type for e in events],
            })

            return world_state, events

    def get_world(self) -> WorldState:
        """Current WorldState without advancing the tick. Used for page hydration."""
        with self._lock:
            metrics = simulation.compute_metrics(self._grid)
            return self._build_world_state(metrics, [])

    def get_cell(self, x: int, y: int) -> dict:
        """Return a dict-copy of cell at column x, row y."""
        with self._lock:
            return copy.copy(self._grid[y][x])

    def get_neighbors_of(self, x: int, y: int) -> list[dict]:
        """Return list of neighbor dicts for cell at (col=x, row=y)."""
        with self._lock:
            return simulation.get_neighbors(self._grid, x, y)

    def apply_agent_action(self, x: int, y: int, action: str) -> dict:
        """
        Apply an authenticated agent action to cell (col=x, row=y).
        Returns the updated cell dict.

        Actions:
          'reforest' — BareSoil → Forest (health 40.0)
          'clear'    — any cell → BareSoil (health 20.0)
          'develop'  — Forest → Urban (health 60.0, stage 1)
        """
        _ACTION_MAP = {
            'reforest': (CellType.BareSoil, CellType.Forest,   40.0),
            'clear':    (None,              CellType.BareSoil,  20.0),
            'develop':  (CellType.Forest,   CellType.Urban,     60.0),
        }
        if not (0 <= x < GRID_WIDTH and 0 <= y < GRID_HEIGHT):
            raise ValueError(f"Agent action coordinates out of bounds: [{x}, {y}]")
        with self._lock:
            cell  = self._grid[y][x]
            entry = _ACTION_MAP.get(action)
            if entry:
                required_type, new_type, new_health = entry
                if required_type is None or cell['type'] == required_type:
                    cell['type']            = new_type
                    cell['health']          = new_health
                    cell['effects']         = []
                    cell['evolution_stage'] = 1
            return copy.copy(cell)

    def reset(self) -> WorldState:
        """Reset to initial conditions. Returns the fresh WorldState."""
        with self._lock:
            self._grid                   = _build_initial_grid()
            self._tick_counter           = 0
            self._urban_milestones_fired = {60: False, 75: False, 90: False}
            self._weather_grid           = _init_weather_grid()
            self._tick_history.clear()
            metrics = simulation.compute_metrics(self._grid)
            return self._build_world_state(metrics, [])

    def get_tick_counter(self) -> int:
        with self._lock:
            return self._tick_counter

    def get_metrics(self) -> dict:
        with self._lock:
            return simulation.compute_metrics(self._grid)

    # ── Private ─────────────────────────────────────────────────────────────────

    def _build_world_state(
        self,
        metrics: dict,
        events:  list[SimulationEvent],
    ) -> WorldState:
        """Convert the internal dict grid to a fully-typed Pydantic WorldState."""
        pydantic_grid: list[list[WorldCell]] = []
        for row in self._grid:
            pydantic_row: list[WorldCell] = []
            for c in row:
                pydantic_row.append(WorldCell(
                    x=c['x'],
                    y=c['y'],
                    type=c['type'],
                    health=round(c['health'], 2),
                    evolution_stage=c.get('evolution_stage', 1),
                    effects=list(c.get('effects', [])),
                ))
            pydantic_grid.append(pydantic_row)

        return WorldState(
            tick_id=self._tick_counter,
            grid=pydantic_grid,
            global_metrics=GlobalMetrics(
                W=round(metrics['W'], 2),
                S=round(metrics['S'], 2),
                F=round(metrics['F'], 2),
                A=round(metrics['A'], 2),
                E=round(metrics['E'], 2),
                C=round(metrics.get('C', 250.0), 2),
            ),
            events=events,
            weather_grid=[
                [
                    WeatherBlock(
                        moisture=blk['moisture'],
                        cloud_cover=blk['cloud_cover'],
                        precipitation=blk['precipitation'],
                    )
                    for blk in row
                ]
                for row in self._weather_grid
            ],
        )


# ─── Global Singleton ─────────────────────────────────────────────────────────
world_engine = WorldEngine()
