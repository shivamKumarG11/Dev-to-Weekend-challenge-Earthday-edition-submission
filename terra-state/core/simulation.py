"""
core/simulation.py — TERRA-STATE: VOX ATLAS v2.0
Per-cell differential equations and World Event system.

All functions here are PURE — they take state in and return new state.
No global mutation. The engine.py state machine calls these functions.
"""
from __future__ import annotations
import copy
import random

from core.models import CellType, SimulationEvent, WeatherBlock

GRID_WIDTH = 200
GRID_HEIGHT = 600

# ─── Physics Coefficients — all tunable ──────────────────────────────────────
# TODO: Copilot Optimization — tune these coefficients against ecological data
WATER_AGRI_DRAIN_RATE    = 0.30   # Water health lost per tick per agri-neighbor ratio
FOREST_REGEN_RATE        = 0.20   # Forest passive health regeneration per tick
FOREST_AGRI_PRESSURE     = 0.15   # Forest health loss per unit of agri-neighbor pressure
AGRI_SOIL_DEPLETION      = 0.60   # Agriculture health lost per tick (soil consumption)
URBAN_HEALTH_GROWTH_RATE = 0.30   # Urban health gained per tick × demand multiplier
BARE_SOIL_DECAY_RATE     = 0.10   # BareSoil slow further degradation per tick
EROSION_HOTSPOT_MULTIPLIER = 3.00 # Forest death leaves highly unstable bare soil
HOTSPOT_WATER_EROSION      = 0.70 # Erosion hotspot penalty on adjacent water cells

# ─── Deep Ecology Thresholds ─────────────────────────────────────────────────────────
DEFORESTATION_A_THRESHOLD = 25.0  # A% above this triggers one deforestation per tick
URBAN_MILESTONES          = [60, 75, 90]   # E% milestones that trigger Urban expansion
CARBON_ACID_RAIN_PPM      = 400.0 # C > 400 triggers global acid rain penalty

# ─── Weather Grid Constants ────────────────────────────────────────────────────
# Coarse weather resolution: each block covers WEATHER_BLOCK cells in each dim.
WEATHER_COLS       = 20    # coarse columns  (200 / 10)
WEATHER_ROWS       = 60    # coarse rows     (600 / 10)
WEATHER_BLOCK      = 10    # cells per block edge

# Evaporation rates (moisture units added per tick)
WEATH_EVAP_OCEAN   = 14.0
WEATH_EVAP_RIVER   = 7.0
WEATH_EVAP_FOREST  = 2.0
WEATH_EVAP_AGRI    = 0.8   # irrigation loss / transpiration
WEATH_EVAP_MELT    = 3.0   # mountain snowmelt

# Cloud & precipitation thresholds
WEATH_CLOUD_THRESH = 45.0  # moisture above this builds cloud
WEATH_PRECIP_THRESH = 0.60 # cloud_cover above this creates rain
WEATH_MOUNTAIN_BONUS = 0.12 # extra cloud_cover per tick (orographic lift)
WEATH_DRIFT_RATE   = 0.28  # fraction of moisture drifting north each tick
WEATH_DISSIPATION  = 2.5   # moisture lost to sky per tick
WEATH_CLOUD_DECAY  = 0.94  # cloud_cover multiplier when no strong source


# ─── Core Utilities ───────────────────────────────────────────────────────────

def clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def get_neighbors(grid: list, x: int, y: int) -> list:
    """
    Return all valid Moore-neighborhood (8-connected) cell dicts for position
    (x=col, y=row). Edge cells have fewer than 8 neighbors.
    """
    neighbors = []
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            nx, ny = x + dx, y + dy
            if 0 <= nx < GRID_WIDTH and 0 <= ny < GRID_HEIGHT:
                neighbors.append(grid[ny][nx])
    return neighbors


def compute_metrics(grid: list) -> dict:
    """
    Derive the 5 global scalar metrics from the current grid.
    Returns a plain dict {W, S, F, A, E} — all floats in [0, 100].
    """
    water_cells = [c for row in grid for c in row if c['type'] == CellType.Water]
    agri_cells  = [c for row in grid for c in row if c['type'] == CellType.Agriculture]
    forest_cells= [c for row in grid for c in row if c['type'] == CellType.Forest]
    urban_cells = [c for row in grid for c in row if c['type'] == CellType.Urban]

    total = GRID_WIDTH * GRID_HEIGHT

    W = sum(c['health'] for c in water_cells) / len(water_cells) if water_cells  else 0.0
    # S = 100 on a pristine world (no farms) — only degrades when Agriculture exists
    S = sum(c['health'] for c in agri_cells)  / len(agri_cells)  if agri_cells   else 100.0
    F = len(forest_cells) / total * 100.0
    A = len(agri_cells)   / total * 100.0
    E = sum(c['health'] for c in urban_cells)  / len(urban_cells)  if urban_cells  else 0.0
    
    # Carbon model: percentage-based so scale doesn't depend on grid size.
    # Baseline 280 ppm (pre-industrial). Forests sink 2.5 ppm per %, urban emits 5,
    # agriculture emits 1.5. Reaches acid-rain threshold (400 ppm) under heavy dev.
    forest_pct = len(forest_cells) / total * 100.0
    urban_pct  = len(urban_cells)  / total * 100.0
    agri_pct   = len(agri_cells)   / total * 100.0
    C = 280.0 + (urban_pct * 5.0) + (agri_pct * 1.5) - (forest_pct * 2.5)
    C = max(100.0, C)

    return {
        'W': round(W, 2),
        'S': round(S, 2),
        'F': round(F, 2),
        'A': round(A, 2),
        'E': round(E, 2),
        'C': round(C, 2),
    }


# ─── Per-Cell Physics ─────────────────────────────────────────────────────────

# ─── Weather Simulation ───────────────────────────────────────────────────────

def _init_weather_grid() -> list[list[dict]]:
    """Return a blank 60-row × 20-col weather grid (list of dicts)."""
    return [
        [{'moisture': 0.0, 'cloud_cover': 0.0, 'precipitation': 0.0}
         for _ in range(WEATHER_COLS)]
        for _ in range(WEATHER_ROWS)
    ]


def tick_weather(
    weather: list[list[dict]],
    grid:    list,
    drought_index: float = 1.0,
) -> list[list[dict]]:
    """
    Advance the coarse weather grid by one tick.

    Pipeline (pure — reads `weather`, writes `new_w`):
      1. Count dominant biome in each block from the cell grid.
      2. Evaporation: inject moisture from ocean/river/forest/mountain sources.
      3. North-drift: 28% of each block's moisture moves to the block above.
      4. Condensation: excess moisture converts to cloud_cover.
             - Mountain blocks (wr ≤ 4) get orographic-lift bonus.
      5. Precipitation: cloud_cover > 0.60 → precipitation spawns,
             draining moisture and eroding cloud_cover.
      6. Dissipation: baseline moisture loss + cloud decay without source.

    drought_index scales down evaporation (dry years → less ocean moisture).
    """
    new_w = [[dict(blk) for blk in row] for row in weather]

    # ── Step 1 + 2: evaporation per block ─────────────────────────────────
    for wr in range(WEATHER_ROWS):
        for wc in range(WEATHER_COLS):
            r0 = wr * WEATHER_BLOCK
            c0 = wc * WEATHER_BLOCK
            # Sample biome counts across the 10×10 cell block
            counts = {t: 0 for t in ('ocean', 'river', 'forest', 'agri', 'mountain')}
            for dr in range(WEATHER_BLOCK):
                for dc in range(WEATHER_BLOCK):
                    r, c = r0 + dr, c0 + dc
                    if r >= GRID_HEIGHT or c >= GRID_WIDTH:
                        continue
                    ct = grid[r][c]['type']
                    if ct == CellType.Water:
                        if r >= 550:   # ocean zone
                            counts['ocean'] += 1
                        else:
                            counts['river'] += 1
                    elif ct == CellType.Forest:
                        counts['forest'] += 1
                    elif ct == CellType.Agriculture:
                        counts['agri'] += 1
                    elif ct == CellType.Mountain:
                        counts['mountain'] += 1

            n = WEATHER_BLOCK * WEATHER_BLOCK
            evap = (
                WEATH_EVAP_OCEAN   * counts['ocean']    / n
                + WEATH_EVAP_RIVER  * counts['river']   / n
                + WEATH_EVAP_FOREST * counts['forest']  / n
                + WEATH_EVAP_AGRI   * counts['agri']    / n
                + WEATH_EVAP_MELT   * counts['mountain'] / n
            ) * (1.0 / max(0.3, drought_index))    # drought_index > 1 → less ocean moisture → less rain (correct)

            new_w[wr][wc]['moisture'] = min(
                100.0, weather[wr][wc]['moisture'] + evap
            )

    # ── Step 3: north-drift ───────────────────────────────────────────────
    # Prevailing wind: south → north (ocean at bottom drifts inland)
    drifted = [[dict(blk) for blk in row] for row in new_w]
    for wr in range(WEATHER_ROWS):
        for wc in range(WEATHER_COLS):
            m = new_w[wr][wc]['moisture']
            transfer = m * WEATH_DRIFT_RATE
            drifted[wr][wc]['moisture']         = max(0.0, m - transfer)
            if wr > 0:   # drift to block above (northward)
                drifted[wr - 1][wc]['moisture'] = min(
                    100.0, drifted[wr - 1][wc]['moisture'] + transfer
                )
    new_w = drifted

    # ── Steps 4+5+6: condensation, precipitation, dissipation ─────────────
    for wr in range(WEATHER_ROWS):
        for wc in range(WEATHER_COLS):
            m  = new_w[wr][wc]['moisture']
            cc = weather[wr][wc]['cloud_cover']    # read from prev tick

            # 4. Condensation
            if m > WEATH_CLOUD_THRESH:
                cc += (m - WEATH_CLOUD_THRESH) * 0.006
            is_mountain_block = (wr <= 4)          # coarse rows 0-4 cover y=0–40
            if is_mountain_block:
                cc += WEATH_MOUNTAIN_BONUS
            cc = min(1.0, cc)

            # 5. Precipitation
            precip = 0.0
            if cc > WEATH_PRECIP_THRESH:
                precip = (cc - WEATH_PRECIP_THRESH) * 2.5
                precip = min(1.0, precip)
                m  = max(0.0, m  - precip * 9.0)
                cc = max(0.0, cc - precip * 0.18)

            # 6. Dissipation (baseline dry-out)
            m  = max(0.0, m - WEATH_DISSIPATION)
            # Slow cloud decay when moisture is low
            if m < WEATH_CLOUD_THRESH * 0.6:
                cc *= WEATH_CLOUD_DECAY

            new_w[wr][wc]['moisture']     = round(min(100.0, m),  3)
            new_w[wr][wc]['cloud_cover']  = round(min(1.0, cc),   4)
            new_w[wr][wc]['precipitation']= round(min(1.0, precip), 4)

    return new_w


def weather_modifiers(
    weather: list[list[dict]],
    col: int,
    row: int,
) -> dict:
    """
    Return {precip, cloud} for the weather block that cell (col, row) falls in.
    """
    wc = min(WEATHER_COLS - 1, col // WEATHER_BLOCK)
    wr = min(WEATHER_ROWS - 1, row // WEATHER_BLOCK)
    blk = weather[wr][wc]
    return {'precip': blk['precipitation'], 'cloud': blk['cloud_cover']}


# ─── Per-Cell Physics ─────────────────────────────────────────────────────────

def apply_physics(
    grid:              list,
    drought_index:     float,
    demand_multiplier: float,
    metrics:           dict,
    weather_grid:      list | None = None,
) -> tuple[list, list[SimulationEvent]]:
    """
    Apply per-cell differential equations to produce a new grid.

    Reads exclusively from `grid`, writes to `new_grid` — no order-of-evaluation
    artifacts. SOIL_DEATH events are emitted here when Agriculture health reaches 0.
    """
    # Fast manual copy — deepcopy on 120k dicts is too slow at tick speed.
    # Cell dicts only contain primitives + one list ('effects'), so dict() + list() suffices.
    new_grid = []
    for _row in grid:
        _new_row = []
        for _cell in _row:
            _c = dict(_cell)
            _c['effects'] = list(_cell['effects'])
            _new_row.append(_c)
        new_grid.append(_new_row)
    events: list[SimulationEvent] = []
    
    E = metrics.get('E', 0.0)
    C = metrics.get('C', 250.0)
    
    # ── Hydrological Mapping (Distance to Water) BFS
    water_dist = [[999] * GRID_WIDTH for _ in range(GRID_HEIGHT)]
    queue = []
    for r in range(GRID_HEIGHT):
        for c in range(GRID_WIDTH):
            if grid[r][c]['type'] == CellType.Water:
                water_dist[r][c] = 0
                queue.append((r, c))
                
    head = 0
    while head < len(queue):
        r, c = queue[head]
        head += 1
        d = water_dist[r][c]
        for dr, dc in [(-1,0), (1,0), (0,-1), (0,1)]:
            nr, nc = r + dr, c + dc
            if 0 <= nr < GRID_HEIGHT and 0 <= nc < GRID_WIDTH:
                if water_dist[nr][nc] == 999:
                    water_dist[nr][nc] = d + 1
                    queue.append((nr, nc))

    for row in range(GRID_HEIGHT):
        for col in range(GRID_WIDTH):
            src  = grid[row][col]
            dest = new_grid[row][col]
            neighbors = get_neighbors(grid, col, row)
            dist_to_water = water_dist[row][col]
            
            # Count toxic blooms in adjacent cells to simulate contagious pollution
            toxic_n = sum(1 for n in neighbors if "TOXIC_BLOOM" in n.get('effects', []))
            hotspot_n = sum(1 for n in neighbors if "EROSION_HOTSPOT" in n.get('effects', []))

            cell_type = src['type']

            # ACID RAIN globally penalizes non-urban tiles
            acid_rain_penalty = 0.5 if C > CARBON_ACID_RAIN_PPM and cell_type != CellType.Urban else 0.0

            # ── Weather modifiers for this cell ────────────────────────
            wx = weather_modifiers(weather_grid, col, row) if weather_grid else {'precip': 0.0, 'cloud': 0.0}
            rain_bonus  = wx['precip']   # 0–1 precipitation intensity
            cloud_cover = wx['cloud']    # 0–1 cloud fraction

            if cell_type == CellType.Water:
                # ── Hydrodynamics: Flow & Volume ──
                # 1. Inflow from North (downward flow propagation)
                inflow = 0.0
                if row > 0:
                    above = grid[row-1][col]
                    if above['type'] == CellType.Water:
                        # Flow moves south (down the grid). Cells receive volume from above.
                        inflow = above['health'] * 0.12
                    elif above['type'] == CellType.Mountain and "GLACIER" in above.get('effects', []):
                        # High Carbon = Heat = Glacial Melt (Source of river)
                        melt_factor = (C / 250.0) * 1.5
                        inflow = melt_factor * (2.0 / max(0.5, drought_index))

                # 2. Outflow / Throughput
                # Rivers are not static pools; they push water downstream.
                outflow = src['health'] * 0.10
                
                # 3. Path Carving (River Physics: Erosion of banks)
                # If volume (health) is extreme, the river overflows/widens
                if src['health'] > 94.0 and row < 550 and random.random() < 0.05:
                    # Pick a random neighbor to convert to water (carving a new path)
                    target = random.choice(neighbors)
                    if target['type'] in (CellType.BareSoil, CellType.Forest, CellType.Agriculture):
                        tx, ty = target['x'], target['y']
                        # Mutate the new_grid directly to carve the path
                        new_grid[ty][tx]['type'] = CellType.Water
                        new_grid[ty][tx]['health'] = 50.0
                        events.append(SimulationEvent(
                            type="RIVER_MEANDER", x=tx, y=ty,
                            description="River volume increased, carving a new tributary path."
                        ))

                # 4. Standard ecological modifiers
                agri_n = sum(1 for n in neighbors if n['type'] == CellType.Agriculture)
                pressure = agri_n / max(len(neighbors), 1)
                # Rain refills rivers
                rain_refill = rain_bonus * 1.2 if src['y'] < 550 else 0.0
                
                # Ocean cells (y > 550) are infinite sinks
                if src['y'] > 550:
                    dest['health'] = 100.0
                else:
                    new_health = src['health'] + inflow - outflow + rain_refill \
                                - (WATER_AGRI_DRAIN_RATE * pressure * drought_index) \
                                - (hotspot_n * HOTSPOT_WATER_EROSION) \
                                - (toxic_n * 0.5)
                    dest['health'] = clamp(new_health, 2.0, 100.0)
                    
                    # 5. Drying up logic (River changes direction by vanishing)
                    if dest['health'] < 6.0:
                        dest['type'] = CellType.BareSoil
                        dest['health'] = 15.0
                        dest['effects'] = [e for e in dest['effects'] if e != "ERODED"]

            elif cell_type == CellType.Urban:
                dest['health'] = clamp(src['health'] + URBAN_HEALTH_GROWTH_RATE * demand_multiplier)
                dest['evolution_stage'] = 3 if E >= 90 else (2 if E >= 60 else 1)
                # Heavy rain → FLOOD_RISK tag
                if rain_bonus > 0.7:
                    if 'FLOOD_RISK' not in dest['effects']:
                        dest['effects'] = list(dest['effects']) + ['FLOOD_RISK']
                else:
                    dest['effects'] = [e for e in dest.get('effects', []) if e != 'FLOOD_RISK']
                if 'HEATWAVE' not in dest['effects'] and C > 500:
                    dest['effects'] = list(dest['effects']) + ['HEATWAVE']
                elif C <= 500 and 'HEATWAVE' in dest['effects']:
                    dest['effects'] = [e for e in dest['effects'] if e != 'HEATWAVE']
                if metrics.get('W', 100.0) < 20.0:
                    if 'INSTABILITY' not in dest['effects']:
                        dest['effects'] = list(dest['effects']) + ['INSTABILITY']
                else:
                    dest['effects'] = [e for e in dest.get('effects', []) if e != 'INSTABILITY']

            elif cell_type == CellType.Forest:
                agri_n = sum(1 for n in neighbors if n['type'] == CellType.Agriculture)
                forest_n = sum(1 for n in neighbors if n['type'] == CellType.Forest)

                # Mycorrhizal Network armor: contiguous forests resist pressure
                armor = 0.8 if forest_n >= 3 else 1.0

                pressure = agri_n / max(len(neighbors), 1)
                drought_penalty = 0.1 * dist_to_water * drought_index
                # Rain boosts forest recovery
                rain_forest_bonus = rain_bonus * 0.25

                forest_health = clamp(
                    src['health']
                    + FOREST_REGEN_RATE
                    + rain_forest_bonus
                    - (FOREST_AGRI_PRESSURE * pressure * armor)
                    - drought_penalty
                    - acid_rain_penalty
                    - (toxic_n * 1.5)
                    - (hotspot_n * 0.4)
                )

                if forest_health <= 0.0:
                    dest['type'] = CellType.BareSoil
                    dest['health'] = 18.0
                    dest['effects'] = ["EROSION_HOTSPOT"]
                    events.append(SimulationEvent(
                        type="FOREST_COLLAPSE",
                        x=col, y=row,
                        description="Forest die-off triggered erosion hotspot chain reaction."
                    ))
                    continue

                dest['health'] = forest_health

                # ── Ecological Spread (Forest Physics) ──
                # Healthy forests in wet conditions expand to neighbors
                if dest['health'] > 90.0 and (rain_bonus > 0.3 or dist_to_water < 2):
                    if random.random() < 0.01:
                        target = random.choice(neighbors)
                        if target['type'] == CellType.BareSoil:
                            tx, ty = target['x'], target['y']
                            new_grid[ty][tx]['type'] = CellType.Forest
                            new_grid[ty][tx]['health'] = 30.0
                            new_grid[ty][tx]['effects'] = []
                            events.append(SimulationEvent(
                                type="FOREST_EXPANSION", x=tx, y=ty,
                                description="Deep forest seeds spreading to adjacent soil."
                            ))

            elif cell_type == CellType.Agriculture:
                # Rainfall partially cancels drought penalty and feeds soil
                effective_drought = max(0.0, drought_index - rain_bonus * 0.8)
                drought_penalty = (0.2 * max(0, dist_to_water - 3)) * effective_drought
                # Direct rain health bonus to soil
                rain_soil_bonus = rain_bonus * 0.35
                cloud_partial = cloud_cover * 0.12   # partial shade reduces evapotranspiration
                new_health = clamp(
                    src['health']
                    - AGRI_SOIL_DEPLETION
                    - drought_penalty
                    + rain_soil_bonus
                    + cloud_partial
                    - acid_rain_penalty
                    - (hotspot_n * 0.35)
                    - (toxic_n * 2.0)
                )
                
                if new_health <= 0.0:
                    dest['type']   = CellType.BareSoil
                    dest['health'] = 20.0
                    dest['effects'] = ["TOXIC_BLOOM"]
                    events.append(SimulationEvent(
                        type="SOIL_DEATH",
                        x=col, y=row,
                        description=f"Agriculture depleted into Toxic Bloom."
                    ))
                else:
                    dest['health'] = new_health

            elif cell_type == CellType.BareSoil:
                erosion_mult = EROSION_HOTSPOT_MULTIPLIER if "EROSION_HOTSPOT" in src.get('effects', []) else 1.0
                dest['health'] = clamp(src['health'] - (BARE_SOIL_DECAY_RATE * erosion_mult) - acid_rain_penalty)
                # slowly wash away toxic blooms
                if "TOXIC_BLOOM" in src.get('effects', []):
                    if random.random() < 0.1:
                        dest['effects'] = [e for e in dest.get('effects', []) if e != "TOXIC_BLOOM"]

    # ── Visual Rule 1: ERODED effect on Water cells when global S < 40 ────────
    S = metrics.get('S', 100.0)
    for row in range(GRID_HEIGHT):
        for col in range(GRID_WIDTH):
            cell = new_grid[row][col]
            if cell['type'] == CellType.Water:
                if S < 40 and "ERODED" not in cell['effects']:
                    cell['effects'] = cell['effects'] + ["ERODED"]
                elif S >= 40 and "ERODED" in cell['effects']:
                    cell['effects'] = [e for e in cell['effects'] if e != "ERODED"]

    return new_grid, events


# ─── World Events ─────────────────────────────────────────────────────────────

def check_deforestation(grid: list, metrics: dict) -> tuple[list, list[SimulationEvent]]:
    """
    Visual Rule 2: If Agriculture percentage exceeds threshold (25%),
    convert the most-pressured Forest cell adjacent to Agriculture into Agriculture.
    """
    events: list[SimulationEvent] = []
    if metrics.get('A', 0.0) <= DEFORESTATION_A_THRESHOLD:
        return grid, events

    # Rank Forest cells by how many Agriculture cells border them
    candidates = []
    for row in range(GRID_HEIGHT):
        for col in range(GRID_WIDTH):
            if grid[row][col]['type'] != CellType.Forest:
                continue
            neighbors = get_neighbors(grid, col, row)
            agri_count = sum(1 for n in neighbors if n['type'] == CellType.Agriculture)
            if agri_count > 0:
                candidates.append((agri_count, row, col))

    if not candidates:
        return grid, events

    candidates.sort(reverse=True)

    # Severity scaling: more cells cleared per tick as Agriculture dominates
    A = metrics.get('A', 0.0)
    conversions = 3 if A > 55.0 else (2 if A > 40.0 else 1)
    for i in range(min(conversions, len(candidates))):
        _, r, c = candidates[i]
        grid[r][c]['type']   = CellType.Agriculture
        grid[r][c]['health'] = 70.0
        grid[r][c]['effects'] = []
        events.append(SimulationEvent(
            type="DEFORESTATION",
            x=c, y=r,
            description=f"Forest [{c},{r}] cleared for Agriculture ({A:.1f}% > {DEFORESTATION_A_THRESHOLD}%)"
        ))
    return grid, events


def check_urban_expansion(
    grid:             list,
    metrics:          dict,
    milestones_fired: dict,
) -> tuple[list, list[SimulationEvent], dict]:
    """
    Visual Rule 3 (Trigger): When Economy E crosses milestones (60, 75, 90)
    for the first time, convert a random Forest cell to Urban (stage 1).
    """
    events: list[SimulationEvent] = []
    E = metrics.get('E', 0.0)
    new_milestones = dict(milestones_fired)

    for threshold in URBAN_MILESTONES:
        if E >= threshold and not milestones_fired.get(threshold, False):
            # Find Forest cells NOT already adjacent to Urban
            candidates = []
            for row in range(GRID_HEIGHT):
                for col in range(GRID_WIDTH):
                    if grid[row][col]['type'] != CellType.Forest:
                        continue
                    neighbors = get_neighbors(grid, col, row)
                    if not any(n['type'] == CellType.Urban for n in neighbors):
                        candidates.append((row, col))

            new_milestones[threshold] = True  # Mark consumed regardless
            if candidates:
                r, c = random.choice(candidates)
                grid[r][c]['type']            = CellType.Urban
                grid[r][c]['health']          = 60.0
                grid[r][c]['evolution_stage'] = 1
                grid[r][c]['effects']         = []
                events.append(SimulationEvent(
                    type="URBAN_EXPANSION",
                    x=c, y=r,
                    description=f"Economy milestone {threshold}% reached → Forest [{c},{r}] → Urban"
                ))

    return grid, events, new_milestones


def check_desertification(grid: list, metrics: dict) -> tuple[list, list[SimulationEvent]]:
    """
    Visual Rule 4: If global Water W reaches 0, ALL remaining Forest cells
    instantly convert to BareSoil — the catastrophic DESERTIFICATION CASCADE.
    """
    events: list[SimulationEvent] = []
    if metrics.get('W', 100.0) > 0.5:   # 0.5 float tolerance
        return grid, events

    forest_count = 0
    for row in range(GRID_HEIGHT):
        for col in range(GRID_WIDTH):
            if grid[row][col]['type'] == CellType.Forest:
                grid[row][col]['type']    = CellType.BareSoil
                grid[row][col]['health']  = 15.0
                grid[row][col]['effects'] = ["DESICCATED"]
                forest_count += 1

    if forest_count > 0:
        events.append(SimulationEvent(
            type="DESERTIFICATION_CASCADE",
            x=None, y=None,
            description=f"HYDROLOGICAL COLLAPSE — {forest_count} Forest cells → BareSoil"
        ))
    return grid, events
