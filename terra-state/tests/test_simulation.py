import pytest
from core.models import WorldCell, CellType
from core.simulation import apply_physics, GRID_SIZE

def test_water_erosion_physics():
    # Setup test grid
    grid = [
        [{'x': x, 'y': y, 'type': CellType.BareSoil, 'health': 50.0, 'effects': [], 'evolution_stage': 1} for x in range(GRID_SIZE)]
        for y in range(GRID_SIZE)
    ]
    grid[0][0] = {'x': 0, 'y': 0, 'type': CellType.Water, 'health': 80.0, 'effects': [], 'evolution_stage': 1}
    
    metrics = {'W': 80.0, 'S': 35.0, 'F': 50.0, 'A': 20.0, 'E': 60.0}  # S < 40 triggers ERODED
    
    new_grid, events = apply_physics(grid, 1.0, 1.0, metrics)
    
    assert "ERODED" in new_grid[0][0]['effects']
