# TERRA-STATE: VOX ATLAS v2.0

> **A real-time gamified macro-economic ecological simulation engine built for the DEV Earth Day Weekend Challenge.**
> Watch forests collapse, rivers turn brown, and cities evolve — all driven by differential equations and live AI analysis.

---

## What It Does

VOX ATLAS simulates a **200×600 planetary grid** (120,000 cells) where every pixel is a mathematical statement about ecological health. Forests generate water. Agriculture depletes soil. Urban sprawl consumes wilderness. Carbon rises as cities expand. And when the system breaks — a **Desertification Cascade** fires and the planet punishes you for it.

The simulation runs on a per-tick differential equation engine. Five global scalar metrics — Water (W), Soil (S), Forest (F), Agriculture (A), Economy (E) — plus Carbon PPM — update every tick based on cell-neighbour pressures, biome interactions, and active agent actions.

**The core message:** ecological and economic systems are inseparable. Daly & Farley's *Ecological Economics* and Meadows' *Thinking in Systems* are encoded into every coefficient.

---

## Features

### The Atlas (Live Canvas)

- Real-time **HTML5 Canvas** rendering of the 200×600 world matrix at 60fps
- Biomes: Water · Forest · Agriculture · Urban (3 evolution stages) · BareSoil · Mountain
- Procedural world generation: U-shaped mountain ranges, sinusoidal river corridors, farmland belts, city clusters
- Zoom + pan (scroll wheel, drag) — inspect individual sectors
- Metric strip with live progress bars for W/S/F/A/E/Carbon
- **Collapse banner** fires when DESERTIFICATION CASCADE triggers

### Oracle View

- Click any tile to open a **sector intelligence report**
- Shows cell type, health, active pressures, neighbour breakdown
- Predicts soil death (ticks remaining before BareSoil conversion)
- Flags deforestation pressure, watershed erosion, urban expansion risk

### Simulation Physics

- Deep Wilderness Invariant: forests >32 cells from any city are protected from agriculture
- Agriculture drains soil at 0.80 health/tick — cells become BareSoil at zero health
- Forests generate watershed moisture; adjacent Agriculture triggers ERODED water effects
- Urban cells evolve through 3 stages (Hut Cluster → Mid-rise → Tower Complex) tied to Economy index
- Carbon PPM rises with urban density and falls with Forest coverage
- Drought severity and market demand override physics via Backboard

### Agent Console

- Submit authenticated spatial actions: **REFOREST**, **CLEAR**, **DEVELOP**
- Full audit ledger with tick, agent ID, action, coordinates, and result
- JWT-authenticated via Auth0 M2M — or use `dev-token` in mock mode

### AI Planetary Advisor

- Gemini Flash reads the current ASCII satellite map + global metrics
- Issues a strict two-sentence radio-comms bulletin identifying the dominant spatial threat
- Alert levels: NOMINAL / ELEVATED / CRITICAL
- Auto-triggers on SOIL_DEATH and CASCADE events

---

## Prize Category Technologies

| Integration | How It's Used |
| --- | --- |
| **Auth0 for Agents** | M2M JWT validation on `POST /agent-request`. JWKS cached async for performance. Mock path uses `dev-token` for local dev. |
| **Google Gemini** | Planetary Advisor sends a 200×600 ASCII satellite map + global metrics to Gemini Flash and receives a tactical two-sentence alert bulletin. |
| **Snowflake** | Fire-and-forget async telemetry sink. Every tick logs `tick_id`, timestamp, and full world state to `TERRA_WORLD_TICKS` table. Non-blocking via `asyncio.create_task`. |
| **Backboard** | Remote config service overriding `drought_severity_index` and `global_market_demand` multipliers that feed directly into the physics engine each tick. |

All integrations **gracefully mock** when API keys are unset — the simulation runs fully without any external accounts.

---

## Architecture

```text
Weekend-Challenge-Earth-Day-Edition/
├── terra-state/
│   ├── app.py                  # FastAPI entry point — routes, error handling, startup log
│   ├── core/
│   │   ├── engine.py           # WorldEngine state machine + threading.Lock
│   │   ├── simulation.py       # Pure differential equations (apply_physics)
│   │   └── models.py           # Pydantic v2 schemas for all data contracts
│   ├── services/
│   │   ├── auth0_guard.py      # Async JWKS fetch + JWT verification
│   │   ├── gemini_oracle.py    # Gemini Flash planetary analysis
│   │   ├── snowflake_log.py    # Async Snowflake telemetry sink
│   │   └── backboard.py        # Remote config multiplier fetcher
│   └── static/
│       ├── index.html          # SPA shell (zero frameworks)
│       ├── renderer.js         # Canvas 2D rendering engine (requestAnimationFrame)
│       ├── app.js              # Frontend controller + event loop
│       └── style.css           # Terminal/trading-desk design system
```

**Domain-driven design**: `core/` has zero knowledge of HTTP or DB. `services/` has zero knowledge of physics. `app.py` is the only integration point.

### API Endpoints

| Method | Path | Description |
| --- | --- | --- |
| `POST` | `/tick` | Advance simulation one tick |
| `GET` | `/world` | Current world state (no tick advance) |
| `GET` | `/oracle/{x}/{y}` | Cell intelligence report for Oracle View |
| `POST` | `/agent-request` | Authenticated spatial agent action |
| `GET` | `/analyze` | Gemini Planetary Advisor bulletin |
| `GET` | `/config` | Current Backboard climate multipliers |
| `POST` | `/reset` | Reset world to initial conditions |
| `GET` | `/api/docs` | Interactive Swagger UI |

---

## Getting Started

### Quick Start (Mock Mode — no API keys needed)

```bash
python run.py
```

That's it. `run.py` creates the virtual environment, installs dependencies, and starts the server. Open `http://localhost:8000`.

All integrations fall back to mock mode when no API keys are set — the simulation runs fully out of the box.

### With Live Integrations

Copy `.env.example` to `terra-state/.env` and fill in your keys, then run:

```bash
cp .env.example terra-state/.env
# edit terra-state/.env
python run.py
```

The server logs which services are configured vs. mocked at startup.

### Docker

```bash
docker-compose -f terra-state/docker-compose.yml up --build
```

---

## Scalability Roadmap

1. **State Externalisation (Redis):** `WorldEngine` currently uses in-memory `threading.Lock`. Horizontal scaling requires migrating the grid state matrix to Redis.
2. **Event Queue (Kafka/RMQ):** `sf_log_tick` is fire-and-forget today — a pub/sub queue would prevent cascading failures if Snowflake rate-limits.
3. **WebSockets:** Replace frontend interval polling with real-time WebSocket tick broadcasts.

---

## Theoretical Foundation

The simulation coefficients are grounded in:

- **Donella H. Meadows — *Thinking in Systems* (2008):** Stock-and-flow relationships, reinforcing feedback loops, leverage points. The river browning as farms expand is a feedback loop made visible.
- **Herman Daly & Joshua Farley — *Ecological Economics* (2004):** The economy as a subsystem of the biosphere. The scale constraint — when Forest coverage collapses, the planet doesn't negotiate.

The Field Manual tab in the app explains the academic lineage behind each simulation rule.

---

## Tech Stack

- **Backend:** FastAPI (async) · Uvicorn · Pydantic v2 · Python 3.11+
- **Frontend:** Vanilla HTML5/CSS3/JS · Canvas 2D API · Chart.js · JetBrains Mono
- **Integrations:** Auth0 M2M · Google Gemini Flash · Snowflake · Backboard SDK
- **Infrastructure:** Docker · Docker Compose
