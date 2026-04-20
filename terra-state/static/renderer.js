'use strict';
/* ═══════════════════════════════════════════════════════════════════════════
   renderer.js — TERRA-STATE: VOX ATLAS v2.0
   HTML5 Canvas Engine — The World Matrix Grid Painter (200×600)

   Architecture:
   - Renderer is an IIFE module exposing a clean public interface.
   - drawFrame(worldState) redraws the 200×600 grid (viewport-culled) on every tick.
   - requestAnimationFrame loop animates Water ripples and Urban beacons only.
   - getTileAt(clientX, clientY): pixel→grid coord conversion (zoom + pan aware).
   - Scroll wheel to zoom (0.5×–20×). Left-drag to pan. Single-click for Oracle View.
   - Legend overlay always drawn at top-right corner of canvas.
   - Visual Rules:
       Rule 1: River water lerps blue→brown based on global Soil Health (S).
       Rule 2: Ocean tiles (y≥550) render as deep navy — distinct from river water.
       Rule 3: Forest→Agri type change auto-redraws on next drawFrame().
       Rule 4: Urban drawUrban() dispatches on cell.evolution_stage.
       Rule 5: BareSoil drawBareSoil() renders after desertification cascade.
       Rule 6: Mountain peaks + glacier caps visible when zoomed in (tile > 4px).
   ═══════════════════════════════════════════════════════════════════════════ */

const Renderer = (() => {

  // ─── Constants ──────────────────────────────────────────────────────────────
  const GRID_WIDTH  = 200;
  const GRID_HEIGHT = 600;
  const MIN_SCALE   = 1.0;
  const MAX_SCALE   = 1.0;

  // ─── State ──────────────────────────────────────────────────────────────────
  let canvas      = null;
  let ctx         = null;
  let containerEl = null;

  let baseTileSize = 3;    // base tile size in canvas pixels at scale=1
  let scale        = 1.0;  // current zoom multiplier
  let offsetX      = 0;    // pan offset X in canvas pixels
  let offsetY      = 0;    // pan offset Y in canvas pixels

  let lastWorldState = null;
  let globalMetrics  = { W: 80, S: 80, F: 80, A: 20, E: 60 };
  let weatherGrid    = null;   // coarse 60×20 weather array from API
  let animFrameId    = null;
  let animT          = 0;
  let hoveredCell    = { x: -1, y: -1 };
  let selectedCell   = { x: -1, y: -1 };
  let onCellClick    = null;

  // ─── Math Utilities ─────────────────────────────────────────────────────────

  function lerp(a, b, t) {
    return a + (b - a) * t;
  }

  function lerpColor(c1, c2, t) {
    const tc = Math.max(0, Math.min(1, t));
    return [
      Math.round(lerp(c1[0], c2[0], tc)),
      Math.round(lerp(c1[1], c2[1], tc)),
      Math.round(lerp(c1[2], c2[2], tc)),
    ];
  }

  function rgb(r, g, b, a = 1) {
    return a < 1 ? `rgba(${r},${g},${b},${a.toFixed(2)})` : `rgb(${r},${g},${b})`;
  }

  // ─── Visual Rule 1: Water / River Color ─────────────────────────────────────
  // When global Soil Health (S) < 40, river water transitions blue→muddy brown.

  function getWaterBaseColor() {
    const S    = globalMetrics.S ?? 80;
    const t    = S < 40 ? Math.min(1, (40 - S) / 40) : 0;
    const blue  = [21, 101, 192];   // #1565c0 clean river
    const brown = [121, 85, 72];    // #795548 eroded river
    return lerpColor(blue, brown, t);
  }

  // ─── Tile: Water (ocean vs river — Visual Rule 2) ────────────────────────────

  function drawWater(cell, px, py) {
    const es      = baseTileSize * scale;
    const isOcean = cell.y >= 550;

    if (isOcean) {
      // Deep navy ocean — visually distinct from river
      ctx.fillStyle = '#06255a';
      ctx.fillRect(px, py, es, es);
      // Slow, wide shimmer
      const ph = cell.x * 0.15 + cell.y * 0.08;
      if (Math.sin(animT * 0.7 + ph) > 0.6) {
        ctx.fillStyle = 'rgba(80, 140, 255, 0.22)';
        const sw = Math.max(1, es * 0.5);
        const sh = Math.max(1, es * 0.2);
        ctx.fillRect(px + es * 0.15, py + es * 0.35, sw, sh);
      }
    } else {
      // River: colour lerps blue→brown as soil health degrades
      const baseColor = getWaterBaseColor();
      ctx.fillStyle = rgb(...baseColor);
      ctx.fillRect(px, py, es, es);
      // Fast ripple flicker
      const ph = cell.x * 0.31 + cell.y * 0.19;
      if (Math.sin((animT + ph) * 2) > 0.8) {
        ctx.fillStyle = 'rgba(255,255,255,0.3)';
        ctx.fillRect(px + es * 0.5, py + es * 0.5, Math.max(1, es * 0.15), Math.max(1, es * 0.15));
      }
    }
  }

  // ─── Tile: Forest ────────────────────────────────────────────────────────────

  function drawForest(cell, px, py) {
    const es       = baseTileSize * scale;
    const vitality = Math.max(0.25, cell.health / 100);
    const g        = Math.round(50 + vitality * 75);
    ctx.fillStyle  = rgb(18, g, 26);
    ctx.fillRect(px, py, es, es);
  }

  // ─── Tile: Agriculture ───────────────────────────────────────────────────────

  function drawAgriculture(cell, px, py) {
    const es         = baseTileSize * scale;
    const t          = cell.health / 100;
    const fieldColor = lerpColor([175, 125, 18], [95, 58, 18], 1 - t);
    ctx.fillStyle    = rgb(...fieldColor);
    ctx.fillRect(px, py, es, es);
    if (cell.health < 30) {
      ctx.fillStyle = 'rgba(0,0,0,0.3)';
      ctx.fillRect(px, py, es, Math.max(1, es * 0.2));
    }
  }

  // ─── Tile: Urban (Visual Rule 4 — stage-based sprite) ────────────────────────

  function drawUrban(cell, px, py) {
    const es = baseTileSize * scale;
    const stage = cell.evolution_stage || 1;
    const colors = [rgb(94, 112, 132), rgb(120, 138, 158), rgb(158, 176, 198)];
    const unstable = cell.effects && cell.effects.includes('INSTABILITY');
    const flicker = unstable ? Math.abs(Math.sin(animT * 13 + cell.x * 0.31 + cell.y * 0.17)) : 1.0;
    const alpha = unstable ? (0.38 + flicker * 0.62) : 1.0;

    ctx.fillStyle = stage === 3 ? `rgba(208,224,240,${alpha.toFixed(3)})` : colors[stage - 1] || colors[0];
    ctx.fillRect(px, py, es, es);

    // White glow for high-value city cores
    if (stage >= 2) {
      const glow = stage === 3 ? 0.6 : 0.35;
      ctx.fillStyle = `rgba(255,255,255,${(glow * alpha).toFixed(3)})`;
      ctx.fillRect(px + es * 0.24, py + es * 0.24, Math.max(1, es * 0.52), Math.max(1, es * 0.52));
    }
    if (stage === 3) {
      ctx.save();
      ctx.shadowColor = `rgba(255,255,255,${(0.7 * alpha).toFixed(3)})`;
      ctx.shadowBlur = Math.max(2, es * 0.9);
      ctx.strokeStyle = `rgba(255,255,255,${(0.45 * alpha).toFixed(3)})`;
      ctx.lineWidth = Math.max(0.6, es * 0.08);
      ctx.strokeRect(px + es * 0.12, py + es * 0.12, es * 0.76, es * 0.76);
      ctx.restore();
    }
  }

  // ─── Tile: BareSoil (Visual Rule 5 — post-desertification) ──────────────────

  function drawBareSoil(cell, px, py) {
    const es   = baseTileSize * scale;
    const t    = cell.health / 100;
    const base = lerpColor([120, 78, 46], [168, 122, 84], t);
    ctx.fillStyle = rgb(...base);
    ctx.fillRect(px, py, es, es);
    if (cell.effects && cell.effects.includes('DESICCATED')) {
      ctx.fillStyle = rgb(180, 130, 90, 0.25);
      ctx.fillRect(px, py, es, es);
    }
    if (cell.effects && cell.effects.includes('EROSION_HOTSPOT')) {
      const pulse = 0.25 + 0.25 * Math.abs(Math.sin(animT * 4 + cell.x * 0.2 + cell.y * 0.1));
      ctx.fillStyle = `rgba(255,140,0,${pulse.toFixed(3)})`;
      ctx.fillRect(px, py, es, es);
    }
  }

  // ─── Tile: Mountain ─────────────────────────────────────────────────────────
  // Mountains occupy y=0–40. elevation=1.0 at y=0 (icecap), 0.0 at y=40 (foothills).
  // ─────────────────────────────────────────────────────────────────────────────

  function drawMountain(cell, px, py) {
    const es = baseTileSize * scale;

    // Normalised elevation: 1.0 at peak row (y=0), 0.0 at treeline (y=40)
    const elevation = Math.max(0, 1.0 - cell.y / 40.0);

    // Deterministic pseudo-random per cell
    const seed = Math.abs(Math.sin(cell.x * 12.9898 + cell.y * 78.233)) * 43758.5453;
    const rand = seed - Math.floor(seed);

    // ── Base colour from elevation ─────────────────────────────────────────────
    // icecap (elev ≈1): #dce9f4 pale blue-white
    // snowline (elev ~0.6): #8fa5b5 grey-blue
    // bare rock (elev ~0.35): #4f5d6b slate
    // foothills (elev ~0): #2d363f dark charcoal
    let baseColor;
    if (elevation > 0.7) {
      // Permanent ice zone: lerp snow-white into grey-blue
      baseColor = lerpColor([143, 165, 181], [220, 233, 244], (elevation - 0.7) / 0.3);
    } else if (elevation > 0.35) {
      // Rocky with intermittent snow patches
      baseColor = lerpColor([79, 93, 107], [143, 165, 181], (elevation - 0.35) / 0.35);
    } else {
      // Foothills / dark rock
      baseColor = lerpColor([45, 54, 63], [79, 93, 107], elevation / 0.35);
    }

    // Subtle per-cell noise so tiles don't look cloned
    const noiseBias = (rand - 0.5) * 18;
    const bc = baseColor.map(c => Math.max(0, Math.min(255, c + noiseBias)));
    ctx.fillStyle = rgb(...bc);
    ctx.fillRect(px, py, es, es);

    // ── Snow overlay on upper tiles (visible at any zoom) ─────────────────────
    if (elevation > 0.55 && rand > 0.35) {
      const snowAlpha = Math.min(0.85, (elevation - 0.55) * 2.5) * ((rand - 0.35) / 0.65);
      ctx.fillStyle = `rgba(235,244,252,${snowAlpha.toFixed(2)})`;
      // Snow patches drift to different quadrants per-cell
      const sx = rand > 0.6 ? 0 : es * 0.3;
      const sy = rand > 0.5 ? 0 : es * 0.2;
      ctx.fillRect(px + sx, py + sy, es * 0.7, es * 0.55);
    }

    // ── Rock face highlight (lit upper-left edge) — visible at any zoom ───────
    if (rand > 0.65 && elevation < 0.75) {
      ctx.fillStyle = `rgba(255,255,255,${(rand - 0.65) * 0.18})`;
      ctx.fillRect(px, py, es * 0.45, es * 0.45);
    }

    // ── Triangle peaks — only shown when zoomed in (es ≥ 5px) ────────────────
    // Higher elevation → more frequent + taller peaks
    const peakChance = 0.06 + elevation * 0.22;   // 6 % at foothills → 28 % at icecap
    if (es >= 5 && rand < peakChance) {
      // Peak height scales with elevation and zoom
      const peakH = es * (2.0 + elevation * 5.0 + rand * 1.5);
      const peakW = es * (1.4 + rand * 1.2);
      const bx    = px + es / 2;
      const by    = py + es;          // base at bottom of tile (peaks extend upward)

      ctx.save();
      ctx.beginPath();
      ctx.rect(0, 0, canvas.width, canvas.height);
      ctx.clip();

      // Rock colour for this peak — lighter at high elevation
      const litFace  = elevation > 0.6 ? '#7a8fa0'  : '#54616d';
      const darkFace = elevation > 0.6 ? '#4a5d6a'  : '#3a4249';

      // Left face (lit by north-east light)
      ctx.beginPath();
      ctx.moveTo(bx, by - peakH);
      ctx.lineTo(bx - peakW / 2, by);
      ctx.lineTo(bx, by);
      ctx.closePath();
      ctx.fillStyle = litFace;
      ctx.fill();

      // Right face (shadowed)
      ctx.beginPath();
      ctx.moveTo(bx, by - peakH);
      ctx.lineTo(bx, by);
      ctx.lineTo(bx + peakW / 2, by);
      ctx.closePath();
      ctx.fillStyle = darkFace;
      ctx.fill();

      // Snow / glacier cap — present above the snowline (elevation > 0.4 and peak is tall)
      const hasGlacier = elevation > 0.38 && peakH > es * 2.5;
      if (hasGlacier) {
        // How much of the peak is glaciated depends on altitude
        const capRatio  = 0.3 + elevation * 0.25;
        const capH = peakH * capRatio;
        const capW = peakW * capRatio;

        ctx.beginPath();
        ctx.moveTo(bx,               by - peakH);
        ctx.lineTo(bx - capW / 2,    by - peakH + capH);
        ctx.lineTo(bx - capW / 4,    by - peakH + capH - es * 0.25);
        ctx.lineTo(bx,               by - peakH + capH + es * 0.15);
        ctx.lineTo(bx + capW / 4,    by - peakH + capH - es * 0.2);
        ctx.lineTo(bx + capW / 2,    by - peakH + capH);
        ctx.closePath();
        // Pure ice-white at very high elevation, bluish-white lower
        const iceR = Math.round(lerpColor([215, 235, 252], [240, 248, 255], elevation)[0]);
        const iceG = Math.round(lerpColor([215, 235, 252], [240, 248, 255], elevation)[1]);
        const iceB = Math.round(lerpColor([215, 235, 252], [240, 248, 255], elevation)[2]);
        ctx.fillStyle = `rgb(${iceR},${iceG},${iceB})`;
        ctx.fill();

        // Glacier shadow (right side)
        ctx.beginPath();
        ctx.moveTo(bx, by - peakH);
        ctx.lineTo(bx, by - peakH + capH + es * 0.15);
        ctx.lineTo(bx + capW / 2, by - peakH + capH);
        ctx.closePath();
        ctx.fillStyle = 'rgba(0,20,60,0.18)';
        ctx.fill();
      }

      ctx.restore();
    }
  }


  // ─── Legend Overlay ──────────────────────────────────────────────────────────

  function drawLegend() {
    const items = [
      { label: 'Mountain', color: '#2d333b' },
      { label: 'Forest',   color: '#198754' },
      { label: 'River',    color: '#00b7ff' },
      { label: 'Ocean',    color: '#06255a' },
      { label: 'Farmland', color: '#d18b1f' },
      { label: 'Urban',    color: '#e7effa' },
      { label: 'BareSoil', color: '#9a6842' },
      { label: 'Cloud',    color: 'rgba(200,215,235,0.6)' },
      { label: 'Rain',     color: 'rgba(100,160,255,0.7)' },
    ];

    ctx.save();
    const pad   = 7;
    const sqSz  = 8;
    const lineH = 14;
    const boxW  = 88;
    const boxH  = items.length * lineH + pad * 2;
    const bx    = canvas.width - boxW - 6;
    const by    = 6;

    ctx.fillStyle   = 'rgba(5,7,10,0.88)';
    ctx.fillRect(bx, by, boxW, boxH);
    ctx.strokeStyle = 'rgba(0,242,255,0.35)';
    ctx.lineWidth   = 0.8;
    ctx.strokeRect(bx + 0.5, by + 0.5, boxW - 1, boxH - 1);

    ctx.font         = '8px "JetBrains Mono", monospace';
    ctx.textBaseline = 'middle';

    items.forEach((item, i) => {
      const ix = bx + pad;
      const iy = by + pad + i * lineH + lineH / 2;
      ctx.fillStyle = item.color;
      ctx.fillRect(ix, iy - sqSz / 2, sqSz, sqSz);
      ctx.fillStyle = '#d7e5fb';
      ctx.fillText(item.label, ix + sqSz + 4, iy);
    });

    // Interaction indicator
    ctx.fillStyle = 'rgba(0,242,255,0.7)';
    ctx.font      = '7px monospace';
    ctx.fillText('click any sector for oracle detail', bx + pad, by + boxH + 12);

    ctx.restore();
  }

  function drawCallouts() {
    const es = baseTileSize * scale;
    const callouts = [
      { label: 'MOUNTAINS', x: 24, y: 18, dx: 88, dy: 30, color: '#dbe8ff' },
      { label: 'OCEAN', x: 160, y: 584, dx: -92, dy: -24, color: '#9ed7ff' },
      { label: 'RIVER CORRIDOR', x: 102, y: 250, dx: 86, dy: -28, color: '#00f2ff' },
      { label: 'FARMLAND BELT', x: 128, y: 378, dx: 84, dy: -24, color: '#ffb460' },
      { label: 'URBAN CLUSTERS', x: 76, y: 430, dx: 96, dy: -18, color: '#ffffff' }
    ];

    ctx.save();
    ctx.lineWidth = 1.2;
    ctx.font = '10px "Space Mono", monospace';
    ctx.textBaseline = 'middle';

    for (const c of callouts) {
      const ax = c.x * es + offsetX;
      const ay = c.y * es + offsetY;
      if (ax < 0 || ax > canvas.width || ay < 0 || ay > canvas.height) continue;

      const lx = ax + c.dx;
      const ly = ay + c.dy;

      ctx.strokeStyle = c.color;
      ctx.beginPath();
      ctx.moveTo(lx, ly);
      ctx.lineTo(ax, ay);
      ctx.stroke();

      const angle = Math.atan2(ay - ly, ax - lx);
      const ah = 6;
      ctx.beginPath();
      ctx.moveTo(ax, ay);
      ctx.lineTo(ax - ah * Math.cos(angle - 0.35), ay - ah * Math.sin(angle - 0.35));
      ctx.lineTo(ax - ah * Math.cos(angle + 0.35), ay - ah * Math.sin(angle + 0.35));
      ctx.closePath();
      ctx.fillStyle = c.color;
      ctx.fill();

      const labelW = ctx.measureText(c.label).width + 12;
      const labelH = 16;
      ctx.fillStyle = 'rgba(8,16,26,0.84)';
      ctx.fillRect(lx - 6, ly - labelH / 2, labelW, labelH);
      ctx.strokeStyle = 'rgba(0,242,255,0.35)';
      ctx.strokeRect(lx - 6, ly - labelH / 2, labelW, labelH);
      ctx.fillStyle = c.color;
      ctx.fillText(c.label, lx, ly);
    }
    ctx.restore();
  }
  // ─── Weather Overlay ─────────────────────────────────────────────────────────
  // Renders cloud cover and animated rain streaks on top of the tile grid.
  // Uses the coarse 20×60 weatherGrid; bilinear interpolation gives smooth edges.

  const WCOLS = 20;  // must match backend WEATHER_COLS
  const WROWS = 60;  // must match backend WEATHER_ROWS
  const WBLK  = 10;  // cells per block edge

  /**
   * Bilinear-sample cloud_cover at fractional block coords (bx, by).
   */
  function sampleCloud(bx, by) {
    if (!weatherGrid || weatherGrid.length === 0) return 0;
    const x0 = Math.floor(bx), y0 = Math.floor(by);
    const x1 = Math.min(WCOLS - 1, x0 + 1), y1 = Math.min(WROWS - 1, y0 + 1);
    const fx = bx - x0, fy = by - y0;
    const v00 = weatherGrid[y0]?.[x0]?.cloud_cover ?? 0;
    const v10 = weatherGrid[y0]?.[x1]?.cloud_cover ?? 0;
    const v01 = weatherGrid[y1]?.[x0]?.cloud_cover ?? 0;
    const v11 = weatherGrid[y1]?.[x1]?.cloud_cover ?? 0;
    return (v00*(1-fx)*(1-fy)) + (v10*fx*(1-fy)) + (v01*(1-fx)*fy) + (v11*fx*fy);
  }

  function samplePrecip(wr, wc) {
    return weatherGrid?.[wr]?.[wc]?.precipitation ?? 0;
  }

  function drawWeatherOverlay() {
    if (!weatherGrid || weatherGrid.length === 0) return;
    const es = baseTileSize * scale;

    // ── Cloud layer: soft semi-transparent quads, bilinearly interpolated ──
    // Sample every WBLK cells and draw one blended rectangle per block
    for (let wr = 0; wr < WROWS; wr++) {
      for (let wc = 0; wc < WCOLS; wc++) {
        // pixel coords of top-left corner of this weather block
        const cellX = wc * WBLK;
        const cellY = wr * WBLK;
        const px = cellX * es + offsetX;
        const py = cellY * es + offsetY;
        const bw = WBLK * es;
        const bh = WBLK * es;

        // Skip if entirely off-screen
        if (px + bw < 0 || px > canvas.width || py + bh < 0 || py > canvas.height) continue;

        const cc = sampleCloud(wc + 0.5, wr + 0.5);
        if (cc < 0.08) continue;  // below visibility threshold — skip

        // Cloud alpha — non-linear so thin cloud is subtle, thick is dense
        const alpha = Math.min(0.72, cc * cc * 1.1);
        // Cloud colour: pure white at high altitude (mountain row), blue-grey lower
        const isMountain = (wr <= 4);
        const cloudR = isMountain ? 235 : 205;
        const cloudG = isMountain ? 245 : 220;
        const cloudB = isMountain ? 255 : 240;
        ctx.fillStyle = `rgba(${cloudR},${cloudG},${cloudB},${alpha.toFixed(3)})`;
        ctx.fillRect(px, py, bw, bh);
      }
    }

    // ── Rain layer: animated vertical streaks on high-precipitation blocks ──
    for (let wr = 0; wr < WROWS; wr++) {
      for (let wc = 0; wc < WCOLS; wc++) {
        const p = samplePrecip(wr, wc);
        if (p < 0.25) continue;

        const cellX = wc * WBLK;
        const cellY = wr * WBLK;
        const px = cellX * es + offsetX;
        const py = cellY * es + offsetY;
        const bw = WBLK * es;
        const bh = WBLK * es;
        if (px + bw < 0 || px > canvas.width || py + bh < 0 || py > canvas.height) continue;

        // Number of streaks proportional to precipitation
        const streakCount = Math.ceil(p * 12);
        const streakAlpha = Math.min(0.75, p * 0.85);
        ctx.strokeStyle = `rgba(130,185,255,${streakAlpha.toFixed(3)})`;
        ctx.lineWidth   = Math.max(0.5, es * 0.08);

        for (let s = 0; s < streakCount; s++) {
          // Deterministic-random streak X within block, animated with animT
          const rx = (wc * 17 + wr * 31 + s * 7) % WBLK;
          const sx = px + (rx / WBLK) * bw;
          // Vertical offset cycles with animT for falling effect
          const phase = (animT * 0.9 + s * 0.4 + wr * 0.15) % 1.0;
          const sy = py + phase * bh - bh * 0.3;
          const len = Math.max(2, es * (0.6 + p * 0.8));
          ctx.beginPath();
          ctx.moveTo(sx, sy);
          ctx.lineTo(sx + es * 0.05, sy + len);
          ctx.stroke();
        }
      }
    }
  }

  // ─── Tile Dispatcher ─────────────────────────────────────────────────────────

  function drawTile(cell, col, row) {
    const es = baseTileSize * scale;
    const px = col * es + offsetX;
    const py = row * es + offsetY;
    const isHovered  = hoveredCell.x  === col && hoveredCell.y  === row;
    const isSelected = selectedCell.x === col && selectedCell.y === row;

    // Draw cell biome
    switch (cell.type) {
      case 'Water':       drawWater(cell, px, py);       break;
      case 'Forest':      drawForest(cell, px, py);      break;
      case 'Agriculture': drawAgriculture(cell, px, py); break;
      case 'Urban':       drawUrban(cell, px, py);       break;
      case 'BareSoil':    drawBareSoil(cell, px, py);    break;
      case 'Mountain':    drawMountain(cell, px, py);    break;
      default:            drawBareSoil(cell, px, py);
    }

    // Dim dying cells
    if (cell.health < 15) {
      ctx.fillStyle = `rgba(0,0,0,${((15 - cell.health) / 15 * 0.45).toFixed(2)})`;
      ctx.fillRect(px, py, es, es);
    }

    // TOXIC_BLOOM effect
    if (cell.effects && cell.effects.includes('TOXIC_BLOOM')) {
      ctx.fillStyle = 'rgba(168,50,201,0.45)';
      ctx.fillRect(px, py, es, es);
      ctx.fillStyle = 'rgba(215,85,255,0.7)';
      for (let i = 0; i < 3; i++) {
        const bx = px + ((col * 31 + row * 17 + i * 5) % (es * 0.8));
        const by = py + ((col * 13 + row * 29 + i * 11) % (es * 0.8));
        ctx.fillRect(bx, by, Math.max(1, es * 0.15), Math.max(1, es * 0.15));
      }
    }

    // FLOOD_RISK effect — blue shimmer on Urban tiles during heavy rain
    if (cell.effects && cell.effects.includes('FLOOD_RISK')) {
      ctx.fillStyle = 'rgba(64,128,255,0.30)';
      ctx.fillRect(px, py, es, es);
    }

    if (cell.health < 20) {
      const pulse = 0.2 + 0.3 * Math.abs(Math.sin(animT * 5 + col * 0.19 + row * 0.23));
      ctx.fillStyle = `rgba(255,140,0,${pulse.toFixed(3)})`;
      ctx.fillRect(px, py, es, es);
    }

    // Grid lines — visible from 1.5× zoom so tile boundaries read clearly
    if (scale >= 1.5) {
      ctx.strokeStyle = 'rgba(30,41,59,0.30)';
      ctx.lineWidth   = 0.4;
      ctx.strokeRect(px + 0.5, py + 0.5, es - 1, es - 1);
    }

    // Hover highlight (only when not selected — prevents double-outline flicker)
    if (isHovered && !isSelected) {
      ctx.strokeStyle = 'rgba(0,242,255,0.65)';
      ctx.lineWidth   = Math.max(1, es * 0.12);
      ctx.strokeRect(px + 1, py + 1, es - 2, es - 2);
    }

    // Selection glow (Oracle View active) — drawn last so it sits on top
    if (isSelected) {
      ctx.save();
      ctx.strokeStyle = '#00f2ff';
      ctx.lineWidth   = Math.max(1.5, es * 0.18);
      ctx.shadowColor = '#00f2ff';
      ctx.shadowBlur  = Math.max(6, es * 0.8);
      ctx.strokeRect(px + 1, py + 1, es - 2, es - 2);
      ctx.restore();
    }
  }

  // ─── Animation Loop (water ripple + stage-3 urban beacon) ────────────────────

  function renderLoop() {
    animT += 0.04;
    if (lastWorldState) {
      const es       = baseTileSize * scale;
      const startCol = Math.max(0, Math.floor(-offsetX / es));
      const startRow = Math.max(0, Math.floor(-offsetY / es));
      const endCol   = Math.min(GRID_WIDTH,  Math.ceil((canvas.width  - offsetX) / es));
      const endRow   = Math.min(GRID_HEIGHT, Math.ceil((canvas.height - offsetY) / es));
      for (let row = startRow; row < endRow; row++) {
        for (let col = startCol; col < endCol; col++) {
          const c = lastWorldState.grid?.[row]?.[col];
          if (!c) continue;
          const isUrbanGlow = c.type === 'Urban' && ((c.evolution_stage || 1) >= 2 || (c.effects || []).includes('INSTABILITY'));
          const isCrisis = c.health < 20 || (c.effects || []).includes('EROSION_HOTSPOT');
          if (c.type === 'Water' || isUrbanGlow || isCrisis) {
            drawTile(c, col, row);
          }
        }
      }
      // Re-render weather overlay on every animation frame (rain animates)
      drawWeatherOverlay();
      drawLegend();
      drawCallouts();
    }
    animFrameId = requestAnimationFrame(renderLoop);
  }

  // ─── Canvas Resize ────────────────────────────────────────────────────────────

  function resizeCanvas() {
    if (!canvas || !containerEl) return;
    const size    = Math.min(containerEl.clientWidth, 600);
    canvas.width  = size;
    canvas.height = size * (GRID_HEIGHT / GRID_WIDTH);
    baseTileSize  = size / GRID_WIDTH;
    scale = 1.0;
    offsetX = 0;
    offsetY = 0;
    if (lastWorldState) drawFrame(lastWorldState);
  }

  // ─── Pan / Zoom Helpers ───────────────────────────────────────────────────────

  function clampOffset() {
    offsetX = 0;
    offsetY = 0;
  }

  // ─── Main Draw Frame ──────────────────────────────────────────────────────────

  /**
   * Redraws the 200×600 grid. Viewport-culled — only visible tiles are painted.
   * Called from app.js on every /tick response and after any zoom/pan change.
   * @param {object} worldState - The WorldState object from the API
   */
  function drawFrame(worldState) {
    if (!ctx) return;
    lastWorldState = worldState;

    ctx.fillStyle = '#05070a';
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    const es       = baseTileSize * scale;
    const startCol = Math.max(0, Math.floor(-offsetX / es));
    const startRow = Math.max(0, Math.floor(-offsetY / es));
    const endCol   = Math.min(GRID_WIDTH,  Math.ceil((canvas.width  - offsetX) / es));
    const endRow   = Math.min(GRID_HEIGHT, Math.ceil((canvas.height - offsetY) / es));

    for (let row = startRow; row < endRow; row++) {
      for (let col = startCol; col < endCol; col++) {
        const cellData = worldState.grid?.[row]?.[col];
        if (!cellData) continue;
        drawTile(cellData, col, row);
      }
    }

    // Draw weather overlay on top of tiles (clouds + rain)
    drawWeatherOverlay();
    drawLegend();
    drawCallouts();
  }

  // ─── Coordinate Conversion ────────────────────────────────────────────────────

  /**
   * Convert client (screen) pixel coords to grid cell {x, y}.
   * Returns null if outside the grid bounds.
   */
  function getTileAt(clientX, clientY) {
    if (!canvas) return null;
    const rect   = canvas.getBoundingClientRect();
    const scaleX = canvas.width  / rect.width;
    const scaleY = canvas.height / rect.height;
    const cx     = (clientX - rect.left) * scaleX;
    const cy     = (clientY - rect.top)  * scaleY;
    const es     = baseTileSize;
    const col    = Math.floor(cx / es);
    const row    = Math.floor(cy / es);
    if (col < 0 || col >= GRID_WIDTH || row < 0 || row >= GRID_HEIGHT) return null;
    return { x: col, y: row };
  }

  // ─── Event Handlers ───────────────────────────────────────────────────────────

  function handleInteraction(clientX, clientY) {
    const tile = getTileAt(clientX, clientY);
    if (!tile) return;
    selectedCell = tile;
    if (lastWorldState) drawFrame(lastWorldState);
    if (onCellClick) onCellClick(tile.x, tile.y);
  }

  function handleClick(e) {
    handleInteraction(e.clientX, e.clientY);
  }

  function handleMouseMove(e) {
    const tile = getTileAt(e.clientX, e.clientY);
    const nx   = tile ? tile.x : -1;
    const ny   = tile ? tile.y : -1;
    if (nx !== hoveredCell.x || ny !== hoveredCell.y) {
      hoveredCell = { x: nx, y: ny };
      canvas.style.cursor = 'crosshair';
    }
  }

  function handleMouseLeave() {
    hoveredCell = { x: -1, y: -1 };
    canvas.style.cursor = 'crosshair';
  }

  function handleTouch(e) {
    e.preventDefault();
    const touch = e.touches[0];
    if (touch) handleInteraction(touch.clientX, touch.clientY);
  }

  // ─── Public Interface ─────────────────────────────────────────────────────────

  /**
   * Initialise the renderer.
   * @param {HTMLCanvasElement} canvasElement
   * @param {HTMLElement}       containerElement - parent for size reference
   * @param {object}            opts             - { onCellClick: (x, y) => void }
   */
  function init(canvasElement, containerElement, opts = {}) {
    canvas      = canvasElement;
    ctx         = canvas.getContext('2d');
    containerEl = containerElement;
    onCellClick = opts.onCellClick || null;

    resizeCanvas();
    window.addEventListener('resize',    resizeCanvas);
    canvas.addEventListener('click',     handleClick);
    canvas.addEventListener('mousemove', handleMouseMove);
    canvas.addEventListener('mouseleave',handleMouseLeave);
    canvas.addEventListener('touchstart',handleTouch,     { passive: false });

    animFrameId = requestAnimationFrame(renderLoop);
  }

  function setGlobalMetrics(metrics) {
    globalMetrics = { ...globalMetrics, ...metrics };
  }

  function setWeatherGrid(wg) {
    weatherGrid = wg || null;
  }

  function clearSelection() {
    selectedCell = { x: -1, y: -1 };
    if (lastWorldState) drawFrame(lastWorldState);
  }

  // Zoom is intentionally disabled for a simplified experience.
  function zoomIn()    { return; }
  function zoomOut()   { return; }
  function resetZoom() { return; }

  function destroy() {
    if (animFrameId) cancelAnimationFrame(animFrameId);
    window.removeEventListener('resize', resizeCanvas);
    if (canvas) {
      canvas.removeEventListener('click',      handleClick);
      canvas.removeEventListener('mousemove',  handleMouseMove);
      canvas.removeEventListener('mouseleave', handleMouseLeave);
      canvas.removeEventListener('touchstart', handleTouch);
    }
  }

  return { init, drawFrame, getTileAt, setGlobalMetrics, setWeatherGrid, clearSelection, zoomIn, zoomOut, resetZoom, destroy };

})();









