"""
services/backboard.py — TERRA-STATE: VOX ATLAS v2.0
Backboard API Integration — Fetches live dynamic climate multipliers from the Backboard Assistant API.
Implements a 15-second cache to prevent blocking the 2-second simulation tick event loop.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Optional

import httpx

from core.models import ConfigMultipliers

log = logging.getLogger("terra-state")

BACKBOARD_API_KEY  = os.getenv("BACKBOARD_API_KEY", "")
BACKBOARD_BASE_URL = "https://app.backboard.io/api"

class BackboardService:
    def __init__(self):
        self.assistant_id: Optional[str] = None
        self.thread_id: Optional[str] = None
        self._last_result = ConfigMultipliers()
        self._last_fetch_time = 0.0
        self._cache_ttl = 15.0  # seconds
        self._lock = asyncio.Lock()

    async def _init_assistant(self, client: httpx.AsyncClient):
        if self.assistant_id and self.thread_id:
            return

        log.info("Initializing Backboard AI Assistant...")
        resp = await client.post(
            f"{BACKBOARD_BASE_URL}/assistants",
            json={
                "name": "VoxAtlas Climate Oracle",
                "system_prompt": (
                    "You are a climate simulation parameter oracle. "
                    "Respond ONLY with valid JSON containing two float keys: "
                    "'drought_severity_index' and 'global_market_demand'. "
                    "For example: {\"drought_severity_index\": 1.1, \"global_market_demand\": 0.95}. "
                    "Do not include markdown formatting or extra text."
                )
            }
        )
        resp.raise_for_status()
        self.assistant_id = resp.json().get("assistant_id")

        resp2 = await client.post(
            f"{BACKBOARD_BASE_URL}/assistants/{self.assistant_id}/threads",
            json={}
        )
        resp2.raise_for_status()
        self.thread_id = resp2.json().get("thread_id")
        log.info(f"Backboard initialized. Assistant={self.assistant_id}, Thread={self.thread_id}")

    async def get_multipliers(
        self,
        drought_override: Optional[float] = None,
        demand_override: Optional[float] = None
    ) -> ConfigMultipliers:
        """
        Fetch dynamic climate multipliers from Backboard AI.
        Caches results for 15s to prevent event loop blocking on fast ticks.
        """
        # 1. Provide requested overrides immediately
        if drought_override is not None or demand_override is not None:
            return ConfigMultipliers(
                drought_severity_index=drought_override if drought_override is not None else self._last_result.drought_severity_index,
                global_market_demand=demand_override if demand_override is not None else self._last_result.global_market_demand,
            )

        # 2. Mock mode fallback
        if not BACKBOARD_API_KEY:
            return ConfigMultipliers()

        # 3. Cache check
        now = time.time()
        if now - self._last_fetch_time < self._cache_ttl:
            return self._last_result

        # 4. Live fetch (locked to prevent stampedes)
        async with self._lock:
            # Double check caching after acquiring lock
            if now - self._last_fetch_time < self._cache_ttl:
                return self._last_result

            try:
                async with httpx.AsyncClient(timeout=8.0, headers={"X-API-Key": BACKBOARD_API_KEY}) as client:
                    await self._init_assistant(client)
                    
                    resp = await client.post(
                        f"{BACKBOARD_BASE_URL}/threads/{self.thread_id}/messages",
                        json={"content": "Provide current climate multipliers as JSON.", "stream": False}
                    )
                    resp.raise_for_status()
                    
                    data = resp.json()
                    content = data.get("content", "{}").strip()
                    
                    if content.startswith("```json"):
                        content = content.replace("```json", "").replace("```", "").strip()
                        
                    parsed = json.loads(content)
                    
                    # Use `or 1.0` to guard against null values in the JSON response
                    drought_val = parsed.get("drought_severity_index") or 1.0
                    demand_val  = parsed.get("global_market_demand")   or 1.0
                    self._last_result = ConfigMultipliers(
                        drought_severity_index=float(drought_val),
                        global_market_demand=float(demand_val),
                    )
                    self._last_fetch_time = now
                    log.info(f"Backboard live multipliers updated: {self._last_result}")

            except Exception as exc:
                log.warning(f"Backboard fetch failed, using last known result: {exc}")

            return self._last_result

backboard_service = BackboardService()
