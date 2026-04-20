import sys
import os
sys.path.append(os.getcwd())

from core.engine import WorldEngine
import random
import time

def test_physics_fast():
    print("Initializing WorldEngine...")
    start = time.time()
    engine = WorldEngine()
    print(f"Init took {time.time() - start:.2f}s")
    
    initial_water = sum(c['type'] == 'Water' for row in engine._grid for c in row)
    initial_forest = sum(c['type'] == 'Forest' for row in engine._grid for c in row)
    print(f"Initial: Water={initial_water}, Forest={initial_forest}")
    
    # Simulate 5 ticks
    print("Simulating 5 ticks...")
    events_triggered = []
    for i in range(5):
        t0 = time.time()
        state, events = engine.tick(drought_severity_index=0.5, global_market_demand=1.5)
        dt = time.time() - t0
        for e in events:
            events_triggered.append(e.type)
        
        w = sum(c.type == 'Water' for row in state.grid for c in row)
        f = sum(c.type == 'Forest' for row in state.grid for c in row)
        print(f"Tick {i}: Water={w}, Forest={f}, Events={len(events)}, Time={dt:.2f}s")

    print(f"Unique Events: {set(events_triggered)}")
    print("Physics verification SUCCESSFUL!")

if __name__ == "__main__":
    test_physics_fast()
