'use strict';
/* ═══════════════════════════════════════════════════════════════════════════
   app.js — TERRA-STATE: VOX ATLAS v2.0
   SPA Controller — Orchestrates the frontend experience

   Responsibilities:
   - Page hydration (GET /world on load)
   - Tick polling engine with speed control (fast/normal/slow)
   - Wires Renderer.drawFrame() to /tick responses
   - Manages Oracle View modal (click tile → GET /oracle/{x}/{y})
   - Agent Request form (POST /agent-request with saved JWT)
   - Gemini AI Advisor terminal (GET /analyze on demand + auto on events)
   - Integration card status checks
   - Event log ticker
   - Collapse banner for DESERTIFICATION_CASCADE
   - localStorage for API keys / JWT
   ═══════════════════════════════════════════════════════════════════════════ */

// ─── DOM References ──────────────────────────────────────────────────────────

const $   = (id)  => document.getElementById(id);
const $$  = (sel) => document.querySelectorAll(sel);

const D = {
  canvas:       $('world-canvas'),
  canvasWrap:   $('canvas-wrapper'),
  tickDisp:     $('tick-display'),
  sidebarTick:  $('sidebar-tick'),

  // Metric bars + vals
  metricsWData: {
    val:  $('val-W'), bar: $('bar-W'),
    val2: $('val-S'), bar2: $('bar-S'),
    val3: $('val-F'), bar3: $('bar-F'),
    val4: $('val-A'), bar4: $('bar-A'),
    val5: $('val-E'), bar5: $('bar-E'),
    val6: $('val-C'), bar6: $('bar-C'),
  },

  sysStatus:    $('sys-status'),
  btnPause:     $('btn-pause'),
  btnReset:     $('btn-reset'),
  eventItems:   $('event-log-items'),

  oracleModal:  $('oracle-modal'),
  oracleClose:  $('oracle-close'),
  oracleBody:   $('oracle-body'),
  oraclePos:    $('oracle-position'),

  collBanner:   $('collapse-banner'),
  collText:     $('collapse-banner-text'),
  collClose:    $('collapse-banner-close'),

  advisorTerm:  $('advisor-terminal'),
  btnAnalyze:   $('btn-analyze'),
  alertLevel:   $('advisor-alert-level'),

  agentIdInput: $('agent-id-input'),
  agentToken:   $('agent-token-input'),
  agentAction:  $('agent-action-select'),
  agentX:       $('agent-x-input'),
  agentY:       $('agent-y-input'),
  agentSubmit:  $('btn-agent-submit'),
  agentResult:  $('agent-result'),
  agentLedger:  $('agent-ledger-body'),

  intAuth0:     $('int-status-auth0'),
  intSnowflake: $('int-status-snowflake'),
  intGemini:    $('int-status-gemini'),
  intBB:        $('int-status-bb'),

  policyDrought: $('policy-drought'),
  policyDemand:  $('policy-demand'),
  policyRelief:  $('policy-relief'),

  speedBtns:    $$('.speed-btn'),
  navItems:     $$('.cockpit-nav__item'),
  tabPanels:    $$('.tab-panel'),
};

// ─── State ───────────────────────────────────────────────────────────────────

const State = {
  worldState: null,
  tickId:     0,
  paused:     false,
  speed:      'normal',      // 'fast' | 'normal' | 'slow'
  tickIntervalMs: { fast: 800, normal: 2000, slow: 4000 },
  tickTimer:  null,
  agentLog:   [],            // [{tick, agentId, action, x, y, status}]
};

// ─── API Helpers ──────────────────────────────────────────────────────────────

const BASE = '';    // same origin

async function apiFetch(url, opts = {}) {
  try {
    const resp = await fetch(BASE + url, {
      headers: { 'Content-Type': 'application/json', ...opts.headers },
      ...opts,
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return await resp.json();
  } catch (err) {
    console.error(`[VOX ATLAS] API error (${url}):`, err);
    return null;
  }
}

// ─── Metric Display ───────────────────────────────────────────────────────────

/**
 * Update the five metric cells (W, S, F, A, E) in the metrics strip.
 * Colour-codes each bar based on thresholds.
 */
function updateMetrics(metrics) {
  if (!metrics) return;

  const defs = [
    { key: 'W', valEl: $('val-W'), barEl: $('bar-W'), warnAt: 50, critAt: 30 },
    { key: 'S', valEl: $('val-S'), barEl: $('bar-S'), warnAt: 40, critAt: 20 },
    { key: 'F', valEl: $('val-F'), barEl: $('bar-F'), warnAt: 30, critAt: 15 },
    { key: 'A', valEl: $('val-A'), barEl: $('bar-A'), warnAt: null, critAt: null, invert: true },
    { key: 'E', valEl: $('val-E'), barEl: $('bar-E'), warnAt: null, critAt: null },
    { key: 'C', valEl: $('val-C'), barEl: $('bar-C'), warnAt: 350, critAt: 400, invert: true },
  ];

  for (const def of defs) {
    const val = metrics[def.key];
    if (val == null) continue;

    // Carbon is in PPM — use dedicated scale and unit, not %
    if (def.key === 'C') {
      def.valEl.textContent = val.toFixed(0) + ' ppm';
      // Scale bar: 100ppm=0%, 800ppm=100%
      const cBarPct = Math.min(100, Math.max(0, (val - 100) / 7));
      def.barEl.style.width = cBarPct.toFixed(1) + '%';
    } else {
      def.valEl.textContent = val.toFixed(1) + '%';
      def.barEl.style.width = Math.min(100, val).toFixed(1) + '%';
    }

    // Clear classes
    def.valEl.classList.remove('warn', 'crit');
    def.barEl.classList.remove('warn', 'crit');

    if (!def.invert) {
      if (def.critAt !== null && val <= def.critAt) {
        def.valEl.classList.add('crit');
        def.barEl.classList.add('crit');
      } else if (def.warnAt !== null && val <= def.warnAt) {
        def.valEl.classList.add('warn');
        def.barEl.classList.add('warn');
      }
    } else {
      if (def.critAt !== null && val >= def.critAt) {
        def.valEl.classList.add('crit');
        def.barEl.classList.add('crit');
      } else if (def.warnAt !== null && val >= def.warnAt) {
        def.valEl.classList.add('warn');
        def.barEl.classList.add('warn');
      }
    }
  }

  // Sync sidebar tick counter
  const tickEl = $('sidebar-tick');
  if (tickEl) tickEl.textContent = State.tickId;

  const tickDisp = $('tick-display');
  if (tickDisp) tickDisp.textContent = State.tickId;
}

// ─── Event Log ────────────────────────────────────────────────────────────────

const EVENT_ICONS = {
  DEFORESTATION: 'DF',
  URBAN_EXPANSION: 'UR',
  SOIL_DEATH: 'SD',
  FOREST_COLLAPSE: 'FC',
  DESERTIFICATION_CASCADE: 'DC',
  ACID_RAIN_FALLOUT: 'AR',
};

function appendEvents(events) {
  if (!events || events.length === 0) return;
  const container = $('event-log-items');
  if (!container) return;

  const emptyEl = container.querySelector('.event-log__empty');
  if (emptyEl) emptyEl.remove();

  // Collapse identical event types this tick: count occurrences
  const typeCounts = {};
  const firstSeen  = {};
  for (const ev of events) {
    typeCounts[ev.type] = (typeCounts[ev.type] || 0) + 1;
    if (!firstSeen[ev.type]) firstSeen[ev.type] = ev;
  }

  for (const [type, ev] of Object.entries(firstSeen)) {
    const count      = typeCounts[type];
    const chip       = document.createElement('span');
    chip.className   = `event-chip ${type}`;
    const icon       = EVENT_ICONS[type] || '⚡';
    const coord      = count === 1 && ev.x != null ? ` [${ev.x},${ev.y}]` : '';
    const countLabel = count > 1 ? ` ×${count}` : '';
    chip.textContent = `T${State.tickId} ${icon} ${type.replace(/_/g,' ')}${coord}${countLabel}`;
    chip.title       = ev.description || '';
    container.prepend(chip);
  }

  // Keep max 12 chips
  const chips = container.querySelectorAll('.event-chip');
  Array.from(chips).slice(12).forEach(c => c.remove());

  // Show DESERTIFICATION COLLAPSE banner
  const cascade = Object.values(firstSeen).find(e => e.type === 'DESERTIFICATION_CASCADE');
  if (cascade) showCollapseBanner(cascade.description);
}

// ─── Collapse Banner ─────────────────────────────────────────────────────────

function showCollapseBanner(msg) {
  const banner = $('collapse-banner');
  const text   = $('collapse-banner-text');
  if (!banner || !text) return;
  text.innerHTML = `⚠ T${State.tickId} — ${msg || 'DESERTIFICATION CASCADE INITIATED'} <span style="opacity:0.6;font-size:0.75em">· Click any orange tile in Oracle View for details</span>`;
  banner.hidden = false;
}

// ─── Tick Engine ──────────────────────────────────────────────────────────────

let tickInFlight      = false;  // prevent overlapping /tick requests
let lastAdvisorCallAt = 0;      // timestamp of last Gemini call
const ADVISOR_COOLDOWN_MS = 30000;  // only auto-call advisor every 30s max

function getPolicyOverrides() {
  let drought = 1.0;
  let demand  = 1.0;

  if (D.policyDrought && D.policyDrought.checked) drought *= 1.35;
  if (D.policyDemand  && D.policyDemand.checked)  demand  *= 1.30;
  if (D.policyRelief  && D.policyRelief.checked)  drought *= 0.75;

  return {
    drought_severity_index: Number(drought.toFixed(3)),
    global_market_demand: Number(demand.toFixed(3)),
  };
}

async function runTick() {
  if (State.paused || tickInFlight) return;
  tickInFlight = true;
  try {
    const data = await apiFetch('/tick', {
      method: 'POST',
      body: JSON.stringify(getPolicyOverrides()),
    });
    if (!data || !data.world_state) return;

    State.worldState = data.world_state;
    State.tickId     = data.tick_id;

    Renderer.setGlobalMetrics(data.global_metrics);
    // Feed live weather grid to renderer every tick so clouds/rain animate correctly
    if (data.world_state && data.world_state.weather_grid) {
      Renderer.setWeatherGrid(data.world_state.weather_grid);
    }
    Renderer.drawFrame(data.world_state);
    updateMetrics(data.global_metrics);
    appendEvents(data.events);

    // Auto-trigger advisor on DESERTIFICATION_CASCADE or SOIL_DEATH — with 30s cooldown
    const now = Date.now();
    const shouldCallAdvisor = data.events && data.events.some(e =>
      e.type === 'DESERTIFICATION_CASCADE' || e.type === 'SOIL_DEATH'
    );
    if (shouldCallAdvisor && now - lastAdvisorCallAt > ADVISOR_COOLDOWN_MS) {
      lastAdvisorCallAt = now;
      setTimeout(fetchAdvisorReport, 800);
    }
  } finally {
    tickInFlight = false;
  }
}

function scheduleTick() {
  if (State.paused) return;
  const ms = State.tickIntervalMs[State.speed] || 2000;
  // Use setTimeout (not setInterval) so next tick only fires AFTER the previous completes
  State.tickTimer = setTimeout(async () => {
    await runTick();
    scheduleTick();
  }, ms);
}

function startTicking() {
  stopTicking();
  scheduleTick();
}

function stopTicking() {
  if (State.tickTimer) { clearTimeout(State.tickTimer); State.tickTimer = null; }
}

// ─── Pause / Resume ───────────────────────────────────────────────────────────

function setPaused(paused) {
  State.paused = paused;
  const statusEl = $('sys-status');

  if (paused) {
    stopTicking();
    if (statusEl) {
      statusEl.className = 'sys-status paused';
      statusEl.innerHTML = '<span class="status-dot"></span> PAUSED';
    }
    const btn = $('btn-pause');
    if (btn) btn.textContent = 'RESUME';
  } else {
    startTicking();
    if (statusEl) {
      statusEl.className = 'sys-status running';
      statusEl.innerHTML = '<span class="status-dot"></span> LIVE';
    }
    const btn = $('btn-pause');
    if (btn) btn.textContent = 'PAUSE';
  }
}

// ─── Oracle View ──────────────────────────────────────────────────────────────

const TYPE_ICONS = {
  Water:       '🌊',
  Forest:      '🌲',
  Agriculture: '🌾',
  Urban:       '🏙',
  BareSoil:    '🏜',
  Mountain:    '⛰',
};

const TYPE_COLORS = {
  Water:       '#1565c0',
  Forest:      '#2e7d32',
  Agriculture: '#f57f17',
  Urban:       '#546e7a',
  BareSoil:    '#795548',
  Mountain:    '#757575',
};

async function openOracle(x, y) {
  const data = await apiFetch(`/oracle/${x}/${y}`);
  if (!data) return;

  const modal = $('oracle-modal');
  const body  = $('oracle-body');
  if (!modal || !body) return;

  const cell    = data.cell;
  const health  = cell.health;
  const icon    = TYPE_ICONS[cell.type]  || '⬡';
  const color   = TYPE_COLORS[cell.type] || '#aaa';

  // Build health bar segments (10 segments)
  const SEG_COUNT = 10;
  const filledCount = Math.round((health / 100) * SEG_COUNT);
  let healthClass = 'filled-high';
  if (health < 30) healthClass = 'filled-crit';
  else if (health < 55) healthClass = 'filled-mid';

  const segs = Array.from({ length: SEG_COUNT }, (_, i) => {
    const filled = i < filledCount;
    return `<div class="oracle-health-seg ${filled ? healthClass : ''}"
      style="flex:1;min-width:10px;"></div>`;
  }).join('');

  // Pressures — fix: class applied correctly as attribute, not text content
  const pressureHtml = data.active_pressures.length
    ? data.active_pressures.map(p =>
        `<div class="oracle-pressure-item${p.startsWith('Stage') ? ' info' : ''}">${p}</div>`
      ).join('')
    : '<div class="oracle-pressure-item info" style="color:var(--text-dim)">— No active pressures detected —</div>';

  // Soil death prediction
  const soilPredHtml = data.predicted_soil_death_in_ticks != null
    ? `<div class="oracle-soil-pred">
         ⏳ Soil collapse predicted in <strong>${data.predicted_soil_death_in_ticks}</strong> ticks
         ${data.predicted_soil_death_in_ticks < 20 ? '— IMMINENT' : ''}
       </div>`
    : '';

  // Stage label for Urban
  const stageHtml = cell.type === 'Urban'
    ? `<div style="font-family:var(--text-mono);font-size:0.6875rem;color:var(--text-dim);margin-top:4px;">
         Stage ${cell.evolution_stage} — ${['Hut Cluster','Mid-rise District','Tower Complex'][cell.evolution_stage - 1]}
       </div>`
    : '';

  // Effects
  const effectsHtml = (cell.effects && cell.effects.length)
    ? `<div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:6px;">
        ${cell.effects.map(e =>
          `<span style="font-family:var(--text-mono);font-size:0.5rem;font-weight:700;
            letter-spacing:0.10em;padding:2px 7px;border-radius:3px;
            color:var(--amber);border:1px solid rgba(255,149,0,0.3);
            background:rgba(255,149,0,0.07);">${e}</span>`
        ).join('')}
       </div>`
    : '';

  body.innerHTML = `
    <div class="oracle-type-row">
      <div class="oracle-type-icon" style="border-color:${color}30;background:${color}15;">
        ${icon}
      </div>
      <div>
        <div class="oracle-type-name">${cell.type.toUpperCase()}</div>
        <div class="oracle-coords">Sector [${x}, ${y}]</div>
        ${stageHtml}
        ${effectsHtml}
      </div>
    </div>

    <div>
      <div class="oracle-stat-label">Biotic Health</div>
      <div class="oracle-health-bar">
        ${segs}
        <span class="oracle-health-pct">${health.toFixed(1)}%</span>
      </div>
    </div>

    <div>
      <div class="oracle-stat-label">Adjacent Sectors</div>
      <div class="oracle-neighbors">
        <div class="oracle-ngb-cell">
          <div class="oracle-ngb-val">${data.forest_neighbors}</div>
          <div class="oracle-ngb-lbl">🌲 Forest</div>
        </div>
        <div class="oracle-ngb-cell">
          <div class="oracle-ngb-val">${data.water_neighbors}</div>
          <div class="oracle-ngb-lbl">🌊 Water</div>
        </div>
        <div class="oracle-ngb-cell">
          <div class="oracle-ngb-val">${data.agriculture_neighbors}</div>
          <div class="oracle-ngb-lbl">🌾 Agri</div>
        </div>
        <div class="oracle-ngb-cell">
          <div class="oracle-ngb-val">${data.urban_neighbors}</div>
          <div class="oracle-ngb-lbl">🏙 Urban</div>
        </div>
        <div class="oracle-ngb-cell">
          <div class="oracle-ngb-val">${data.bare_soil_neighbors}</div>
          <div class="oracle-ngb-lbl">🏜 Bare</div>
        </div>
      </div>
    </div>

    ${soilPredHtml}

    <div>
      <div class="oracle-stat-label">Active Pressures</div>
      <div class="oracle-pressures">${pressureHtml}</div>
    </div>
  `;

  modal.hidden = false;
}

function closeOracle() {
  const modal = $('oracle-modal');
  if (!modal) return;
  modal.style.opacity = '0';
  modal.style.pointerEvents = 'none';
  setTimeout(() => {
    modal.hidden = true;
    modal.style.opacity = '';
    modal.style.pointerEvents = '';
  }, 180);
  Renderer.clearSelection();
}

// ─── AI Advisor Terminal ──────────────────────────────────────────────────────

let typewriterInterval = null;  // module-level so it can be cancelled on tab switch

async function fetchAdvisorReport() {
  const term = $('advisor-terminal');
  if (!term) return;

  // Cancel any in-progress typewriter before starting a new one
  if (typewriterInterval) { clearInterval(typewriterInterval); typewriterInterval = null; }

  term.innerHTML = `<span class="t-tick">TICK #${State.tickId}</span>  <span class="t-prompt">VOX ATLAS AI &gt;</span>  <span class="t-cursor">█</span> <span style="color:var(--text-dim)">Contacting Planetary Advisor…</span>`;

  const data = await apiFetch('/analyze');
  if (!data) {
    term.innerHTML += '\n<span style="color:var(--red)">CONNECTION FAILED — Advisor unreachable.</span>';
    return;
  }

  const report = data.analyst_report || '';
  const levelMatch = report.match(/\[ALERT LEVEL:\s*(NOMINAL|ELEVATED|CRITICAL)\]/i);
  const level = levelMatch ? levelMatch[1].toUpperCase() : 'NOMINAL';

  // Update badge
  const badge = $('advisor-alert-level');
  if (badge) {
    badge.textContent = level;
    badge.className = `advisor-alert-level ${level}`;
  }

  // Typewriter effect
  term.innerHTML = `<span class="t-tick">TICK #${State.tickId}</span>  <span class="t-prompt">VOX ATLAS AI &gt;</span>\n\n`;
  const reportSpan = document.createElement('span');
  reportSpan.className = 't-report';
  reportSpan.textContent = '';
  term.appendChild(reportSpan);

  let i = 0;
  typewriterInterval = setInterval(() => {
    if (i >= report.length) {
      clearInterval(typewriterInterval);
      typewriterInterval = null;
      return;
    }
    reportSpan.textContent += report[i++];
    term.scrollTop = term.scrollHeight;
  }, 12);
}

// ─── Agent Request Form ───────────────────────────────────────────────────────


async function submitAgentRequest() {
  const agentId = ($('agent-id-input') || {}).value?.trim();
  const token   = ($('agent-token-input') || {}).value?.trim() || 'dev-token';
  const action  = ($('agent-action-select') || {}).value;
  const x       = parseInt(($('agent-x-input') || {}).value);
  const y       = parseInt(($('agent-y-input') || {}).value);
  const result  = $('agent-result');

  if (!agentId || isNaN(x) || isNaN(y)) {
    if (result) { result.textContent = '⚠ Fill in all fields.'; result.style.color = 'var(--amber)'; }
    return;
  }

  // Client-side bounds validation — prevents sending illegal coords to server
  if (x < 0 || x > 199 || y < 0 || y > 599) {
    if (result) {
      result.textContent = `⚠ Out of bounds. X must be 0–199, Y must be 0–599. Got [${x}, ${y}].`;
      result.style.color = 'var(--amber)';
    }
    return;
  }

  if (result) { result.textContent = '⏳ Submitting…'; result.style.color = 'var(--text-dim)'; }

  const data = await apiFetch('/agent-request', {
    method: 'POST',
    headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ agent_id: agentId, action, x, y }),
  });

  if (data && data.status === 'applied') {
    if (result) { result.textContent = `✓ Applied: ${action} at [${x},${y}]`; result.style.color = 'var(--green)'; }
    State.agentLog.unshift({ tick: State.tickId, agentId, action, x, y, status: 'ok' });
    renderAgentLedger();
    // Clear coordinate inputs so it's obvious the action went through
    const xEl = $('agent-x-input'), yEl = $('agent-y-input');
    if (xEl) xEl.value = '';
    if (yEl) yEl.value = '';
    // Clear the result message after 4s
    setTimeout(() => { if (result) result.textContent = ''; }, 4000);
    // Force a tick to see the change immediately
    await runTick();
  } else {
    const detail = data?.detail || 'Unknown error';
    if (result) { result.textContent = `✕ ${detail}`; result.style.color = 'var(--red)'; }
    State.agentLog.unshift({ tick: State.tickId, agentId, action, x, y, status: 'err' });
    renderAgentLedger();
  }
}

function renderAgentLedger() {
  const tbody = $('agent-ledger-body');
  if (!tbody) return;
  if (!State.agentLog.length) {
    tbody.innerHTML = `<tr><td colspan="6" style="color:var(--text-dim);text-align:center;padding:16px 0;">
      — No agent actions yet —</td></tr>`;
    return;
  }
  tbody.innerHTML = State.agentLog.slice(0, 12).map(entry => `
    <tr>
      <td>${entry.tick}</td>
      <td>${entry.agentId}</td>
      <td>${entry.action}</td>
      <td>${entry.x}</td>
      <td>${entry.y}</td>
      <td class="ledger-${entry.status}">${entry.status === 'ok' ? '✓ Applied' : '✕ Rejected'}</td>
    </tr>
  `).join('');
}

// ─── Integration Card Status ──────────────────────────────────────────────────

function checkIntStatus() {
  const keys = {
    auth0:     localStorage.getItem('vox_auth0_domain'),
    snowflake: localStorage.getItem('vox_sf_account'),
    gemini:    localStorage.getItem('vox_gemini_key'),
    bb:        localStorage.getItem('vox_bb_key'),
  };

  for (const [key, val] of Object.entries(keys)) {
    const el = $(`int-status-${key}`);
    if (!el) continue;
    if (val) {
      el.textContent = 'CONFIGURED';
      el.className   = 'int-card__status configured';
    } else {
      el.textContent = 'MOCK';
      el.className   = 'int-card__status';
    }
  }
}

function saveIntegrationKey(storageKey, inputId, confirmId) {
  const input = $(inputId);
  const val   = input ? input.value.trim() : '';
  const conf  = $(confirmId);
  if (val) localStorage.setItem(storageKey, val);
  if (conf) {
    conf.hidden = false;
    setTimeout(() => { conf.hidden = true; }, 2200);
  }
  checkIntStatus();
}

// ─── Speed Control ────────────────────────────────────────────────────────────

function setSpeed(speed) {
  State.speed = speed;
  D.speedBtns.forEach(btn => {
    btn.classList.toggle('active', btn.dataset.speed === speed);
  });
  if (!State.paused) startTicking();
}

// ─── Tab Navigation ───────────────────────────────────────────────────────────

function activateTab(tabId) {
  // Cancel typewriter if leaving the command view
  if (tabId !== 'command' && typewriterInterval) {
    clearInterval(typewriterInterval);
    typewriterInterval = null;
  }
  D.navItems.forEach(item => item.classList.toggle('active', item.dataset.tab === tabId));
  D.tabPanels.forEach(panel => {
    panel.classList.toggle('active', panel.id === `tab-${tabId}`);
    panel.hidden = panel.id !== `tab-${tabId}`;
  });
}

// ─── Page Hydration ───────────────────────────────────────────────────────────

async function hydratePage() {
  const data = await apiFetch('/world');
  if (!data) return;

  State.worldState = data.world_state;
  State.tickId     = data.tick_id;

  Renderer.setGlobalMetrics(data.global_metrics);
  if (data.world_state && data.world_state.weather_grid) {
    Renderer.setWeatherGrid(data.world_state.weather_grid);
  }
  Renderer.drawFrame(data.world_state);
  updateMetrics(data.global_metrics);
}

// ─── Reset ────────────────────────────────────────────────────────────────────

async function resetSim() {
  stopTicking();
  const data = await apiFetch('/reset', { method: 'POST' });
  if (data && data.world_state) {
    State.worldState = data.world_state;
    const resetMetrics =
      data.global_metrics ||
      data.world_state.global_metrics || { W: 0, S: 0, F: 0, A: 0, E: 0, C: 0 };
    State.tickId = data.tick_id ?? data.world_state.tick_id ?? 0;
    Renderer.setGlobalMetrics(resetMetrics);
    Renderer.drawFrame(data.world_state);
    updateMetrics(resetMetrics);
    const logEl = $('event-log-items');
    if (logEl) logEl.innerHTML = '<span class="event-log__empty">— No events yet —</span>';
    const banner = $('collapse-banner');
    if (banner) banner.hidden = true;
  }
  if (!State.paused) startTicking();
}

// ─── Boot ─────────────────────────────────────────────────────────────────────

async function boot() {
  // 1. Initialise canvas renderer
  Renderer.init(D.canvas, D.canvasWrap, {
    onCellClick: (x, y) => openOracle(x, y),
  });

  // 2. Hydrate with current world state
  await hydratePage();

  // 3. Start tick polling
  startTicking();

  // 4. Check saved integration keys
  checkIntStatus();

  // 5. Restore saved input values from localStorage
  const savedId = localStorage.getItem('vox_agent_id');
  if (savedId && $('agent-id-input')) $('agent-id-input').value = savedId;
  if (D.policyDrought) D.policyDrought.checked = localStorage.getItem('vox_policy_drought') === '1';
  if (D.policyDemand) D.policyDemand.checked = localStorage.getItem('vox_policy_demand') === '1';
  if (D.policyRelief) D.policyRelief.checked = localStorage.getItem('vox_policy_relief') === '1';

  // 6. Wire event listeners ─────────────────────────────────────────────────

  // Oracle close
  $('oracle-close').addEventListener('click', closeOracle);
  $('oracle-modal').addEventListener('click', (e) => {
    if (e.target === $('oracle-modal')) closeOracle();
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeOracle(); });

  // Collapse banner close
  $('collapse-banner-close').addEventListener('click', () => {
    $('collapse-banner').hidden = true;
  });

  // Pause / Resume
  $('btn-pause').addEventListener('click', () => setPaused(!State.paused));

  // Reset
  $('btn-reset').addEventListener('click', resetSim);

  // Speed buttons
  D.speedBtns.forEach(btn => {
    btn.addEventListener('click', () => setSpeed(btn.dataset.speed));
  });

  // Policy switches
  if (D.policyDrought) {
    D.policyDrought.addEventListener('change', () => {
      localStorage.setItem('vox_policy_drought', D.policyDrought.checked ? '1' : '0');
    });
  }
  if (D.policyDemand) {
    D.policyDemand.addEventListener('change', () => {
      localStorage.setItem('vox_policy_demand', D.policyDemand.checked ? '1' : '0');
    });
  }
  if (D.policyRelief) {
    D.policyRelief.addEventListener('change', () => {
      localStorage.setItem('vox_policy_relief', D.policyRelief.checked ? '1' : '0');
    });
  }

  // Tab navigation
  D.navItems.forEach(item => {
    item.addEventListener('click', () => activateTab(item.dataset.tab));
  });

  // AI Advisor button
  const btnAnalyze = $('btn-analyze');
  if (btnAnalyze) btnAnalyze.addEventListener('click', fetchAdvisorReport);

  // Agent form submit
  const btnAgent = $('btn-agent-submit');
  if (btnAgent) btnAgent.addEventListener('click', submitAgentRequest);

  // Save agent ID to localStorage
  const agentIdInput = $('agent-id-input');
  if (agentIdInput) {
    agentIdInput.addEventListener('change', () => {
      localStorage.setItem('vox_agent_id', agentIdInput.value.trim());
    });
  }

  // Integration save buttons — confirmId must match the actual HTML element IDs
  const saveMap = [
    ['save-auth0-btn',      'vox_auth0_domain',  'int-auth0-input',     'save-confirm-auth0'],
    ['save-snowflake-btn',  'vox_sf_account',    'int-sf-input',        'save-confirm-sf'],
    ['save-gemini-btn',     'vox_gemini_key',    'int-gemini-input',    'save-confirm-gemini'],
    ['save-bb-btn',         'vox_bb_key',        'int-bb-input',        'save-confirm-bb'],
  ];
  for (const [btnId, storeKey, inputId, confirmId] of saveMap) {
    const btn = $(btnId);
    if (btn) btn.addEventListener('click', () => saveIntegrationKey(storeKey, inputId, confirmId));
  }

  // Zoom interactions intentionally removed for simpler map navigation.

  // Sidebar pause shortcut (keyboard)
  document.addEventListener('keydown', (e) => {
    if (e.key === ' ' && e.target.tagName !== 'INPUT' && e.target.tagName !== 'SELECT') {
      e.preventDefault();
      setPaused(!State.paused);
    }
  });

  // Warm start the oracle log in command mode
  setTimeout(fetchAdvisorReport, 600);

  console.log('%c[VOX ATLAS] SPA boot complete', 'color:#00f2ff;font-weight:bold');
}

// Fire boot after DOM is ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot);
} else {
  boot();
}



