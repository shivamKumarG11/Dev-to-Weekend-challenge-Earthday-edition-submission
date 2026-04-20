"""
services/gemini_oracle.py — TERRA-STATE: VOX ATLAS v2.0
Gemini Planetary Advisor — analyzes the world's ASCII satellite map.

Mock path:  GEMINI_API_KEY unset → returns a deterministic advisory.
Live path:  google-genai SDK, gemini-3-flash-preview, asyncio.to_thread().
"""
from __future__ import annotations
import asyncio
import logging
import os

from core.models import WorldState, CellType

log = logging.getLogger("terra-state")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY",   "")
GEMINI_MODEL   = "gemini-3-flash-preview"      # Per resource spec

# ASCII representation for each cell type in the prompt map
_CELL_SYMBOLS = {
    CellType.Water:       "W",
    CellType.Forest:      "F",
    CellType.Agriculture: "A",
    CellType.Urban:       "U",
    CellType.BareSoil:    "B",
}


def _build_ascii_map(world_state: WorldState) -> str:
    """Render the N×N grid as a readable ASCII satellite map for the prompt."""
    size = len(world_state.grid)
    header = "     " + "".join(f"{i:02d} " for i in range(size))
    sep    = "    " + "─" * (size * 3)
    lines  = [header, sep]
    for r, row in enumerate(world_state.grid):
        cells = "  ".join(_CELL_SYMBOLS.get(c.type, "?") for c in row)
        lines.append(f" {r:02d}| {cells}")
    return "\n".join(lines)


async def gemini_analyze(world_state: WorldState) -> str:
    """
    Submit the current World Matrix state to Gemini for a two-sentence
    Planetary Advisor radio-comms alert. Returns the report string.
    """
    m   = world_state.global_metrics
    t   = world_state.tick_id

    # ── Mock path ────────────────────────────────────────────────────────────
    if not GEMINI_API_KEY:
        risk = (
            "CRITICAL" if (m.W < 30 or m.S < 15) else
            "ELEVATED" if (m.W < 55 or m.S < 35) else
            "NOMINAL"
        )
        if risk == "CRITICAL":
            detail = (
                f"Water reserves at {m.W:.1f}% and soil health at {m.S:.1f}% — "
                f"agricultural sector accelerating hydrological collapse."
            )
            directive = "Immediate reforestation and irrigation reduction required to prevent DESERTIFICATION CASCADE."
        elif risk == "ELEVATED":
            detail = (
                f"Soil erosion index elevated — river corridor showing {m.S:.1f}% soil health; "
                f"deforestation pressure active at {m.F:.1f}% forest coverage."
            )
            directive = f"Recommend deploying reforest agents to sectors with bare-soil adjacency. Economy at {m.E:.1f}%."
        else:
            detail = (
                f"All ecological systems within nominal parameters at tick #{t}: "
                f"Water {m.W:.1f}% · Soil {m.S:.1f}% · Forest {m.F:.1f}% · C_PPM {m.C:.1f}."
            )
            directive = f"Monitor Agriculture expansion ({m.A:.1f}%) — urban economy at {m.E:.1f}% approaching next evolution milestone."

        return f"[ALERT LEVEL: {risk}]: {detail} {directive}"

    # ── Live Gemini path ──────────────────────────────────────────────────────
    ascii_map = _build_ascii_map(world_state)
    prompt = (
        "You are the VOX ATLAS Planetary AI Advisor — a senior strategic intelligence "
        "officer monitoring a real-time satellite ecological simulation. "
        "Grid legend: W=Water · F=Forest · A=Agriculture · U=Urban · B=BareSoil.\n\n"
        f"SATELLITE MAP (TICK #{t}):\n{ascii_map}\n\n"
        f"GLOBAL METRICS: "
        f"Water={m.W:.1f}% | Soil={m.S:.1f}% | Forest={m.F:.1f}% | "
        f"Agriculture={m.A:.1f}% | Economy={m.E:.1f}% | Carbon PPM={m.C:.1f}\n\n"
        "Deliver a strict two-sentence radio-comms advisory. "
        "Sentence 1: Identify the dominant SPATIAL threat visible on the map, "
        "citing specific grid coordinates or zones. "
        "Sentence 2: Issue a precise, actionable directive. "
        "Tone: authoritative, technical, no hedging. No filler phrases. "
        "Prefix format: [ALERT LEVEL: NOMINAL|ELEVATED|CRITICAL]:"
    )

    def _call_gemini_sync() -> str:
        from google import genai
        client   = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        return response.text.strip()

    try:
        result = await asyncio.to_thread(_call_gemini_sync)
        log.info(f"Gemini Planetary Advisor report received ({len(result)} chars)")
        return result
    except Exception as exc:
        log.error(f"Gemini API call failed: {exc}")
        return f"[ADVISORY SYSTEM OFFLINE]: Communication link to Gemini disrupted. Error: {exc}"
