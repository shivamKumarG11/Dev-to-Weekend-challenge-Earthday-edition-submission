"""
services/snowflake_log.py — TERRA-STATE: VOX ATLAS v2.0
Async Snowflake spatial tick logging.

Inserts the full 10×10 grid snapshot as JSONB into TERRA_WORLD_TICKS on every tick.
Uses asyncio.to_thread() because snowflake-connector-python is synchronous.
Non-fatal — errors are logged only, never raised to the caller.
"""
from __future__ import annotations
import asyncio
import json
import logging
import os

from core.models import WorldState

log = logging.getLogger("terra-state")

SF_ACCOUNT   = os.getenv("SNOWFLAKE_ACCOUNT",   "")
SF_USER      = os.getenv("SNOWFLAKE_USER",       "")
SF_PASSWORD  = os.getenv("SNOWFLAKE_PASSWORD",   "")
SF_DATABASE  = os.getenv("SNOWFLAKE_DATABASE",   "TERRA_DB")
SF_SCHEMA    = os.getenv("SNOWFLAKE_SCHEMA",     "PUBLIC")
SF_WAREHOUSE = os.getenv("SNOWFLAKE_WAREHOUSE",  "TERRA_WH")

_DDL   = """
CREATE TABLE IF NOT EXISTS TERRA_WORLD_TICKS (
    tick_id        INT             NOT NULL,
    grid_snapshot  VARIANT         NOT NULL      COMMENT '120000-cell JSON grid (200x600)',
    global_W       FLOAT,
    global_S       FLOAT,
    global_F       FLOAT,
    global_A       FLOAT,
    global_E       FLOAT,
    global_C       FLOAT,
    events_json    VARIANT                        COMMENT 'Event types this tick',
    created_at     TIMESTAMP_NTZ   DEFAULT CURRENT_TIMESTAMP
)
"""
_INSERT = """
INSERT INTO TERRA_WORLD_TICKS
    (tick_id, grid_snapshot, global_W, global_S, global_F, global_A, global_E, global_C, events_json)
VALUES (%s, PARSE_JSON(%s), %s, %s, %s, %s, %s, %s, PARSE_JSON(%s))
"""


async def sf_log_tick(tick_id: int, world_state: WorldState) -> None:
    """
    Asynchronously log a full spatial tick to Snowflake.
    Fire-and-forget - call with asyncio.create_task() from app.py.
    """
    m = world_state.global_metrics

    # ── Mock path ────────────────────────────────────────────────────────────
    if not all([SF_ACCOUNT, SF_USER, SF_PASSWORD]):
        event_types = [e.type for e in (world_state.events or [])]
        log.info(
            f"[MOCK Snowflake] tick={tick_id:04d} "
            f"W={m.W:.1f} S={m.S:.1f} F={m.F:.1f} A={m.A:.1f} E={m.E:.1f} C={m.C:.1f} "
            f"events={event_types or '[]'}"
        )
        return

    # ── Live path ─────────────────────────────────────────────────────────────
    grid_json = json.dumps([
        [
            {
                'x':               c.x,
                'y':               c.y,
                'type':            c.type.value,   # enum → string for JSON serialization
                'health':          c.health,
                'evolution_stage': c.evolution_stage,
                'effects':         c.effects,
            }
            for c in row
        ]
        for row in world_state.grid
    ])

    events_json = json.dumps([e.type for e in (world_state.events or [])])

    def _blocking_insert() -> None:
        import snowflake.connector
        conn = snowflake.connector.connect(
            account=SF_ACCOUNT,
            user=SF_USER,
            password=SF_PASSWORD,
            database=SF_DATABASE,
            schema=SF_SCHEMA,
            warehouse=SF_WAREHOUSE,
        )
        try:
            cur = conn.cursor()
            cur.execute(_DDL)
            cur.execute(_INSERT, (
                tick_id, grid_json,
                m.W, m.S, m.F, m.A, m.E, m.C,
                events_json,
            ))
            conn.commit()
            log.info(f"Snowflake ✓ TERRA_WORLD_TICKS tick={tick_id}")
        finally:
            conn.close()

    try:
        await asyncio.to_thread(_blocking_insert)
    except Exception as exc:
        log.error(f"Snowflake log failed (tick={tick_id}): {exc}")
