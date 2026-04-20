import sys
import os
sys.path.append(os.getcwd())

from core.engine import WorldEngine
import random

def test_physics():
    print("Initializing WorldEngine...")
    engine = WorldEngine()
    
    initial_water = sum(c['type'] == 'Water' for row in engine._grid for c in row)
    initial_forest = sum(c['type'] == 'Forest' for row in engine._grid for c in row)
    print(f"Initial: Water={initial_water}, Forest={initial_forest}")
    
    # Simulate 50 ticks with high carbon to trigger melt and expansion
    print("Simulating 50 ticks...")
    events_triggered = set()
    for i in range(50):
        # Pass high demand and drought index to see effects
        state, events = engine.tick(drought_severity_index=0.5, global_market_demand=1.5)
        for e in events:
            events_triggered.add(e.type)
        
        if i % 10 == 0:
            w = sum(c.type == 'Water' for row in state.grid for c in row)
            f = sum(c.type == 'Forest' for row in state.grid for c in row)
            print(f"Tick {i}: Water={w}, Forest={f}, Events={len(events)}")

    print(f"Events triggered overall: {events_triggered}")
    
    assert "RIVER_MEANDER" in events_triggered or "FOREST_EXPANSION" in events_triggered or initial_water != sum(c.type == 'Water' for row in state.grid for c in row), "No dynamic physics observed!"
    print("Physics verification SUCCESSFUL!")

if __name__ == "__main__":
    test_physics()
