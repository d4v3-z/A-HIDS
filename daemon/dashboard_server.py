#!/usr/bin/env python3
"""
A-HIDS Tactical C2 Operations Console & Web Dashboard (Anti-Slop High-Craft Design)
Server-Sent Events (SSE) live streaming from incident_forensic.log,
Hardware Interlink state monitoring, and interactive tactical test triggers.
"""

import sys
import os
import time
import json
import socket
import urllib.request
import urllib.parse
from datetime import datetime
from flask import Flask, Response, jsonify, render_template_string, request

app = Flask(__name__)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
LOG_FILE = os.path.join(PROJECT_DIR, "logs", "incident_forensic.log")
CONFIG_FILE = os.path.join(SCRIPT_DIR, "ahids_config.json")
PORT = 5050

def get_master_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def get_esp32_ip():
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r") as f:
                cfg = json.load(f)
                if "esp32_ip" in cfg and cfg["esp32_ip"]:
                    return cfg["esp32_ip"]
    except Exception:
        pass
    return "192.168.0.50"

def get_esp32_url():
    return f"http://{get_esp32_ip()}"

# HTML DASHBOARD TEMPLATE (Anti-Slop Tactical SOC Console)
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>A-HIDS // TACTICAL C2 COMMAND MATRIX</title>
  <style>
    :root {
      --bg-dark: #080a0f;
      --bg-panel: #0e121a;
      --bg-subpanel: #141924;
      --border-dim: #1e2535;
      --border-bright: #2d374d;
      --text-main: #d1d5db;
      --text-dim: #64748b;
      --text-bright: #f8fafc;
      --color-green: #00ff66;
      --color-amber: #f59e0b;
      --color-red: #ef4444;
      --color-cyan: #00e5ff;
      --color-purple: #a855f7;
      --font-mono: "JetBrains Mono", "Fira Code", "Courier New", Courier, monospace;
      --font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }
    
    body {
      background-color: var(--bg-dark);
      color: var(--text-main);
      font-family: var(--font-sans);
      font-size: 13px;
      line-height: 1.5;
      overflow-x: hidden;
      background-image: 
        radial-gradient(circle at 50% 0%, rgba(0, 229, 255, 0.03) 0%, transparent 60%),
        linear-gradient(rgba(30, 37, 53, 0.2) 1px, transparent 1px),
        linear-gradient(90deg, rgba(30, 37, 53, 0.2) 1px, transparent 1px);
      background-size: 100% 100%, 32px 32px, 32px 32px;
    }

    /* TOP CLASSIFICATION BANNER */
    .top-banner {
      background: #0b0e14;
      border-bottom: 1px solid var(--border-dim);
      padding: 8px 20px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-family: var(--font-mono);
      font-size: 11px;
      letter-spacing: 0.05em;
    }
    .badge-live {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      color: var(--color-green);
      font-weight: bold;
    }
    .dot-pulse {
      width: 7px;
      height: 7px;
      background-color: var(--color-green);
      border-radius: 50%;
      box-shadow: 0 0 8px var(--color-green);
      animation: pulse 1.8s infinite;
    }
    @keyframes pulse {
      0%, 100% { opacity: 1; transform: scale(1); }
      50% { opacity: 0.4; transform: scale(0.85); }
    }

    /* HEADER */
    header {
      padding: 16px 20px;
      border-bottom: 1px solid var(--border-dim);
      display: flex;
      justify-content: space-between;
      align-items: center;
      background: rgba(14, 18, 26, 0.85);
      backdrop-filter: blur(8px);
    }
    .brand-title {
      font-family: var(--font-mono);
      font-size: 17px;
      font-weight: 700;
      color: var(--text-bright);
      letter-spacing: 0.08em;
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .brand-title span {
      color: var(--color-cyan);
    }
    .sys-specs {
      font-family: var(--font-mono);
      font-size: 11px;
      color: var(--text-dim);
      display: flex;
      gap: 16px;
    }
    .sys-spec-item b {
      color: var(--text-main);
    }

    /* MAIN CONTAINER */
    .dashboard-grid {
      display: grid;
      grid-template-columns: 320px 380px 1fr;
      gap: 16px;
      padding: 16px 20px;
      max-width: 1700px;
      margin: 0 auto;
    }

    @media (max-width: 1200px) {
      .dashboard-grid { grid-template-columns: 1fr; }
    }

    /* CARD PANELS */
    .panel {
      background: var(--bg-panel);
      border: 1px solid var(--border-dim);
      border-radius: 2px;
      display: flex;
      flex-direction: column;
      position: relative;
    }
    .panel::before {
      content: "+";
      position: absolute;
      top: -6px;
      left: -4px;
      font-family: var(--font-mono);
      font-size: 10px;
      color: var(--border-bright);
    }
    .panel::after {
      content: "+";
      position: absolute;
      top: -6px;
      right: -4px;
      font-family: var(--font-mono);
      font-size: 10px;
      color: var(--border-bright);
    }

    .panel-header {
      padding: 10px 14px;
      background: var(--bg-subpanel);
      border-bottom: 1px solid var(--border-dim);
      font-family: var(--font-mono);
      font-size: 11px;
      font-weight: 600;
      letter-spacing: 0.08em;
      color: var(--text-bright);
      text-transform: uppercase;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .panel-header .tag {
      font-size: 9px;
      padding: 2px 6px;
      background: rgba(0, 229, 255, 0.1);
      color: var(--color-cyan);
      border: 1px solid rgba(0, 229, 255, 0.25);
    }

    .panel-body {
      padding: 14px;
      flex: 1;
    }

    /* TOPOLOGY NODES */
    .node-box {
      background: var(--bg-subpanel);
      border: 1px solid var(--border-dim);
      padding: 12px;
      margin-bottom: 12px;
      position: relative;
    }
    .node-title {
      font-family: var(--font-mono);
      font-size: 12px;
      font-weight: 700;
      color: var(--text-bright);
      display: flex;
      justify-content: space-between;
      margin-bottom: 6px;
    }
    .node-meta {
      font-family: var(--font-mono);
      font-size: 11px;
      color: var(--text-dim);
      display: grid;
      grid-template-columns: 80px 1fr;
      gap: 4px;
    }
    .node-meta span.val {
      color: var(--text-main);
    }

    /* HARDWARE BUS MONITOR */
    .bus-container {
      background: #090c12;
      border: 1px solid var(--border-dim);
      padding: 10px;
      font-family: var(--font-mono);
      font-size: 10px;
      margin-top: 10px;
    }
    .bus-header {
      color: var(--color-cyan);
      font-weight: bold;
      margin-bottom: 8px;
      text-transform: uppercase;
    }
    .pin-row {
      display: flex;
      justify-content: space-between;
      padding: 3px 0;
      border-bottom: 1px dashed rgba(30, 37, 53, 0.6);
    }
    .pin-row:last-child { border-bottom: none; }
    .pin-sig { color: var(--text-dim); }
    .pin-state { color: var(--color-green); font-weight: bold; }
    .pin-state.warn { color: var(--color-amber); }
    .pin-state.danger { color: var(--color-red); }

    /* METRIC GAUGES */
    .stat-grid {
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 10px;
      margin-bottom: 14px;
    }
    .stat-cell {
      background: var(--bg-subpanel);
      border: 1px solid var(--border-dim);
      padding: 10px;
    }
    .stat-label {
      font-family: var(--font-mono);
      font-size: 10px;
      color: var(--text-dim);
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }
    .stat-val {
      font-family: var(--font-mono);
      font-size: 22px;
      font-weight: 700;
      color: var(--text-bright);
      margin-top: 4px;
    }
    .stat-val.red { color: var(--color-red); }
    .stat-val.cyan { color: var(--color-cyan); }
    .stat-val.green { color: var(--color-green); }

    /* PILLAR MATRIX */
    .pillar-row {
      padding: 8px 10px;
      background: var(--bg-subpanel);
      border-left: 3px solid var(--color-cyan);
      margin-bottom: 8px;
      font-family: var(--font-mono);
      font-size: 11px;
    }
    .pillar-row.p1 { border-left-color: var(--color-red); }
    .pillar-row.p2 { border-left-color: var(--color-amber); }
    .pillar-row.p3 { border-left-color: var(--color-purple); }
    .pillar-row.p4 { border-left-color: var(--color-green); }
    .pillar-title {
      font-weight: bold;
      color: var(--text-bright);
      display: flex;
      justify-content: space-between;
    }
    .pillar-sub {
      color: var(--text-dim);
      font-size: 10px;
      margin-top: 2px;
    }

    /* TELEMETRY FEED */
    .feed-container {
      background: #06080d;
      border: 1px solid var(--border-dim);
      padding: 8px;
      font-family: var(--font-mono);
      font-size: 11px;
      height: 440px;
      overflow-y: auto;
      scrollbar-width: thin;
      scrollbar-color: var(--border-bright) transparent;
    }
    .feed-line {
      padding: 4px 6px;
      margin-bottom: 2px;
      border-left: 2px solid transparent;
      white-space: pre-wrap;
      word-break: break-all;
    }
    .feed-line.event { border-left-color: var(--color-amber); background: rgba(245, 158, 11, 0.04); }
    .feed-line.critical { border-left-color: var(--color-red); background: rgba(239, 68, 68, 0.08); color: #fca5a5; font-weight: bold; }
    .feed-line.lockdown { border-left-color: var(--color-red); background: rgba(239, 68, 68, 0.15); color: #ff8080; }
    .feed-line.heartbeat { color: #475569; }
    .feed-line.info { border-left-color: var(--color-cyan); color: #7dd3fc; }

    /* ACTION BAR */
    .actions-grid {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 8px;
      margin-top: 12px;
    }
    button.btn-tactical {
      background: var(--bg-subpanel);
      border: 1px solid var(--border-bright);
      color: var(--text-bright);
      font-family: var(--font-mono);
      font-size: 10px;
      font-weight: 600;
      letter-spacing: 0.05em;
      padding: 10px 8px;
      cursor: pointer;
      text-transform: uppercase;
      transition: all 0.15s ease;
      text-align: center;
    }
    button.btn-tactical:hover {
      background: rgba(0, 229, 255, 0.1);
      border-color: var(--color-cyan);
      color: var(--color-cyan);
    }
    button.btn-tactical.danger {
      border-color: rgba(239, 68, 68, 0.4);
    }
    button.btn-tactical.danger:hover {
      background: rgba(239, 68, 68, 0.15);
      border-color: var(--color-red);
      color: var(--color-red);
    }
    button:disabled {
      opacity: 0.4;
      cursor: not-allowed;
    }
  </style>
</head>
<body>

  <!-- TOP CLASSIFICATION -->
  <div class="top-banner">
    <div>
      <span style="color: var(--color-cyan);">SECURITY CLASSIFICATION:</span> SENSITIVE INTERNAL HARDWARE TELEMETRY
    </div>
    <div style="display: flex; gap: 20px;">
      <div>ESP32: <span id="top-esp-status" style="color: var(--color-green);">CHECKING...</span></div>
      <div>SENTINEL: <span style="color: var(--color-cyan);">AIR-GAPPED (D2/D7/D8)</span></div>
      <div class="badge-live"><div class="dot-pulse"></div> TELEMETRY ACTIVE</div>
    </div>
  </div>

  <!-- HEADER -->
  <header>
    <div class="brand-title">
      <span>//</span> A-HIDS TACTICAL COMMAND & AUDIT CONSOLE
    </div>
    <div class="sys-specs">
      <div class="sys-spec-item">TARGET: <b>10.211.137.55:80/23</b></div>
      <div class="sys-spec-item">C2 BUS: <b>/dev/ttyACM0 (115200)</b></div>
      <div class="sys-spec-item">UDP DEFENSE: <b>PORT 9999</b></div>
      <div class="sys-spec-item">CLOCK: <b id="sys-clock">--:--:--</b></div>
    </div>
  </header>

  <!-- DASHBOARD GRID -->
  <main class="dashboard-grid">

    <!-- COLUMN 1: HARDWARE TOPOLOGY -->
    <div class="panel">
      <div class="panel-header">
        <span>[01] Hardware Topology</span>
        <span class="tag">PHYSICAL BUS</span>
      </div>
      <div class="panel-body">
        
        <!-- THE BAIT -->
        <div class="node-box">
          <div class="node-title">
            <span>THE BAIT (Honeypot)</span>
            <span style="color: var(--color-green);" id="node-esp32-badge">ONLINE</span>
          </div>
          <div class="node-meta">
            <span>Device:</span><span class="val">ESP32 DevKit V1</span>
            <span>IP Addr:</span><span class="val">10.211.137.55</span>
            <span>SSID:</span><span class="val">M@rx! (2.4GHz)</span>
            <span>Services:</span><span class="val">HTTP :80 // Telnet :23</span>
          </div>
        </div>

        <!-- THE SENTINEL -->
        <div class="node-box">
          <div class="node-title">
            <span>THE GHOST SENTINEL</span>
            <span style="color: var(--color-cyan);">AIR-GAPPED</span>
          </div>
          <div class="node-meta">
            <span>Device:</span><span class="val">Arduino Uno / Nano</span>
            <span>Serial:</span><span class="val">/dev/ttyACM0 @ 115200</span>
            <span>Watchdog:</span><span class="val">15.0s Fail-Secure</span>
            <span>Clock RTC:</span><span class="val">DS1302 Standalone</span>
          </div>
        </div>

        <!-- PHYSICAL INTERLINK BUS -->
        <div class="bus-container">
          <div class="bus-header">// Out-of-Band Hardware Interlink</div>
          <div class="pin-row">
            <span class="pin-sig">TX2 (GPIO17) &rarr; Uno D2</span>
            <span class="pin-state">1-WAY UART [9600]</span>
          </div>
          <div class="pin-row">
            <span class="pin-sig">EN Pin &larr; Uno D7</span>
            <span class="pin-state">OPEN-DRAIN RESET</span>
          </div>
          <div class="pin-row">
            <span class="pin-sig">GPIO27 &larr; Uno D8</span>
            <span class="pin-state">PURGE STRAP NVS</span>
          </div>
          <div class="pin-row">
            <span class="pin-sig">Common GND &harr; GND</span>
            <span class="pin-state">VOLTAGE REF 0V</span>
          </div>
        </div>

        <!-- TRUSTED NODE DAEMON -->
        <div class="node-box" style="margin-top: 12px; margin-bottom: 0;">
          <div class="node-title">
            <span>ACTIVE DEFENSE CLIENTS</span>
            <span style="color: var(--color-purple);" id="trusted-node-badge">ARMED</span>
          </div>
          <div class="node-meta">
            <span>Broadcast:</span><span class="val">255.255.255.255:9999</span>
            <span>Defense:</span><span class="val">iptables DROP / Auto-Quarantine</span>
            <span>Key Auth:</span><span class="val">SECRET_MASTER_NANO_...</span>
          </div>
        </div>

      </div>
    </div>

    <!-- COLUMN 2: DEFENSE METRICS & 4 PILLARS -->
    <div class="panel">
      <div class="panel-header">
        <span>[02] Multi-Layer Defense Matrix</span>
        <span class="tag">ANALYTICS</span>
      </div>
      <div class="panel-body">

        <!-- STATS NUMBERS -->
        <div class="stat-grid">
          <div class="stat-cell">
            <div class="stat-label">Total Events</div>
            <div class="stat-val cyan" id="stat-events">0</div>
          </div>
          <div class="stat-cell">
            <div class="stat-label">Hard Lockdowns</div>
            <div class="stat-val red" id="stat-lockdowns">0</div>
          </div>
          <div class="stat-cell">
            <div class="stat-label">Entropy Exploits</div>
            <div class="stat-val" id="stat-entropy">0</div>
          </div>
          <div class="stat-cell">
            <div class="stat-label">Canary Triggers</div>
            <div class="stat-val red" id="stat-canary">0</div>
          </div>
        </div>

        <!-- 4 PILLARS BREAKDOWN -->
        <div style="font-family: var(--font-mono); font-size: 10px; color: var(--color-cyan); margin-bottom: 8px; text-transform: uppercase;">
          // Multi-Pillar Detection Architecture
        </div>

        <div class="pillar-row p1">
          <div class="pillar-title">
            <span>PILAR 1: CANARY TRIPWIRES</span>
            <span style="color: var(--color-red);">+100 PTS</span>
          </div>
          <div class="pillar-sub">Endpoints: /.env, /admin_secret, /wp-login.php, Port 23 (Telnet)</div>
        </div>

        <div class="pillar-row p2">
          <div class="pillar-title">
            <span>PILAR 2: WAF HEURISTIC SIGNATURES</span>
            <span style="color: var(--color-amber);">+35 PTS</span>
          </div>
          <div class="pillar-sub">SQLi, CMDi, LFI Traversal, XSS Injection, Scanner User-Agents</div>
        </div>

        <div class="pillar-row p3">
          <div class="pillar-title">
            <span>PILAR 3: SHANNON ENTROPY MATH</span>
            <span style="color: var(--color-purple);">+35 PTS</span>
          </div>
          <div class="pillar-sub">H &gt; 4.5 (Shellcode Base64) // H &lt; 1.8 (NOP Sled Buffer Overflow)</div>
        </div>

        <div class="pillar-row p4">
          <div class="pillar-title">
            <span>PILAR 4: SLIDING WINDOW FREQUENCY</span>
            <span style="color: var(--color-green);">+10 / HIT</span>
          </div>
          <div class="pillar-sub">T1/2 = 60s Exponential Decay // Tarpit Delay & Rate-Limiter</div>
        </div>

        <!-- MTTR BENCHMARK -->
        <div style="background: var(--bg-subpanel); border: 1px solid var(--border-dim); padding: 10px; margin-top: 12px; font-family: var(--font-mono); font-size: 11px;">
          <div style="color: var(--text-dim); font-size: 10px; text-transform: uppercase;">BENCHMARK HARDWARE RESILIENCE</div>
          <div style="display: flex; justify-content: space-between; margin-top: 4px;">
            <span>Mean Recovery (MTTR):</span>
            <b style="color: var(--color-green);">5.44 Detik</b>
          </div>
          <div style="display: flex; justify-content: space-between; margin-top: 2px;">
            <span>Sterilization Rate:</span>
            <b style="color: var(--color-green);">100% (Clean NVS/RAM)</b>
          </div>
        </div>

      </div>
    </div>

    <!-- COLUMN 3: REAL-TIME TELEMETRY FEED & TACTICAL ACTIONS -->
    <div class="panel">
      <div class="panel-header">
        <span>[03] Real-Time Forensic Stream</span>
        <span class="tag" style="background: rgba(0, 255, 102, 0.1); color: var(--color-green); border-color: rgba(0, 255, 102, 0.3);">SSE STREAMING</span>
      </div>
      <div class="panel-body">
        
        <!-- LOG FEED TERMINAL -->
        <div class="feed-container" id="feed-container">
          <div class="feed-line info">[INIT] Menghubungkan ke aliran forensik Server-Sent Events...</div>
        </div>

        <!-- TACTICAL TEST FIRE PANEL -->
        <div style="margin-top: 12px;">
          <div style="font-family: var(--font-mono); font-size: 10px; color: var(--text-dim); text-transform: uppercase; margin-bottom: 6px;">
            // Tactical Test-Fire Actions (Live Verification)
          </div>
          <div class="actions-grid">
            <button class="btn-tactical" onclick="triggerTest('sqli')">Test SQLi</button>
            <button class="btn-tactical" onclick="triggerTest('telnet')">Port 23 Telnet</button>
            <button class="btn-tactical" onclick="triggerTest('canary')">Canary /.env</button>
            <button class="btn-tactical danger" onclick="triggerTest('entropy')">Entropy Shellcode</button>
          </div>
        </div>

      </div>
    </div>

  </main>

  <script>
    // System Clock
    function updateClock() {
      const d = new Date();
      document.getElementById('sys-clock').innerText = d.toTimeString().split(' ')[0];
    }
    setInterval(updateClock, 1000);
    updateClock();

    // Check ESP32 Status
    async function checkESPStatus() {
      try {
        const res = await fetch('/api/check_esp');
        const data = await res.json();
        const elTop = document.getElementById('top-esp-status');
        const elBadge = document.getElementById('node-esp32-badge');
        if (data.online) {
          elTop.innerText = 'ONLINE (' + data.latency_ms + 'ms)';
          elTop.style.color = 'var(--color-green)';
          elBadge.innerText = 'ONLINE';
          elBadge.style.color = 'var(--color-green)';
        } else {
          elTop.innerText = 'REBOOTING / PURGE';
          elTop.style.color = 'var(--color-red)';
          elBadge.innerText = 'PURGE REBOOT';
          elBadge.style.color = 'var(--color-red)';
        }
      } catch (e) {}
    }
    setInterval(checkESPStatus, 3000);
    checkESPStatus();

    // Stats Polling
    async function updateStats() {
      try {
        const res = await fetch('/api/stats');
        const data = await res.json();
        document.getElementById('stat-events').innerText = data.events_count;
        document.getElementById('stat-lockdowns').innerText = data.lockdowns_count;
        document.getElementById('stat-entropy').innerText = data.entropy_count;
        document.getElementById('stat-canary').innerText = data.canary_count;
      } catch (e) {}
    }
    setInterval(updateStats, 2000);
    updateStats();

    // Real-Time SSE Log Stream
    const feed = document.getElementById('feed-container');
    const evtSource = new EventSource('/api/stream');

    evtSource.onmessage = function(e) {
      if (!e.data || e.data.trim() === '') return;
      const line = e.data;
      const div = document.createElement('div');
      div.className = 'feed-line';

      if (line.includes('LOCKDOWN|') || line.includes('HARDWARE REBOOT')) {
        div.className += ' lockdown';
      } else if (line.includes('CRITICAL')) {
        div.className += ' critical';
      } else if (line.includes('EVENT|')) {
        div.className += ' event';
      } else if (line.includes('HB,')) {
        div.className += ' heartbeat';
      } else {
        div.className += ' info';
      }

      div.innerText = line;
      feed.appendChild(div);

      // Keep only last 150 lines to maintain smooth performance
      while (feed.children.length > 150) {
        feed.removeChild(feed.firstChild);
      }
      feed.scrollTop = feed.scrollHeight;
    };

    // Tactical Test Fire
    async function triggerTest(testType) {
      const btns = document.querySelectorAll('button.btn-tactical');
      btns.forEach(b => b.disabled = true);
      try {
        const res = await fetch('/api/trigger?type=' + testType, { method: 'POST' });
        const data = await res.json();
        console.log('Test trigger response:', data);
      } catch (e) {
        console.error(e);
      } finally {
        setTimeout(() => {
          btns.forEach(b => b.disabled = false);
        }, 3000);
      }
    }
  </script>
</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(DASHBOARD_HTML)

@app.route("/api/check_esp")
def check_esp():
    t0 = time.time()
    try:
        req = urllib.request.Request(f"{ESP32_URL}/", headers={"User-Agent": "A-HIDS-HealthMonitor"})
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            lat = round((time.time() - t0) * 1000, 1)
            return jsonify({"online": resp.getcode() == 200, "latency_ms": lat})
    except Exception:
        return jsonify({"online": False, "latency_ms": 0})

@app.route("/api/stats")
def get_stats():
    events_count = 0
    lockdowns_count = 0
    entropy_count = 0
    canary_count = 0

    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "r", errors="ignore") as f:
            for line in f:
                if "EVENT|" in line:
                    events_count += 1
                if "LOCKDOWN|" in line:
                    lockdowns_count += 1
                if "ENCRYPTED_SHELLCODE" in line or "NOP_SLED" in line:
                    entropy_count += 1
                if "CANARY_" in line or "IOT_BOTNET" in line:
                    canary_count += 1

    return jsonify({
        "events_count": events_count,
        "lockdowns_count": lockdowns_count,
        "entropy_count": entropy_count,
        "canary_count": canary_count
    })

@app.route("/trusted_node.py")
@app.route("/client")
def download_client():
    client_path = os.path.join(SCRIPT_DIR, "trusted_node.py")
    if os.path.exists(client_path):
        with open(client_path, "r") as f:
            return Response(f.read(), mimetype="text/plain")
    return Response("# Client not found", status=404)

@app.route("/win")
@app.route("/win.ps1")
def download_windows_bootstrap():
    master_ip = get_master_ip()
    ps_script = f"""# A-HIDS Windows Fast Client Provisioner
Write-Host "[*] Mengunduh A-HIDS Endpoint Defender dari Master C2 ({master_ip})..." -ForegroundColor Cyan
$dest = "$env:TEMP\\ahids_trusted_node.py"
Invoke-WebRequest -Uri "http://{master_ip}:5050/trusted_node.py" -OutFile $dest -UseBasicParsing

Write-Host "[+] Binary siap. Menjalankan A-HIDS Sentinel Listener..." -ForegroundColor Green
python $dest
"""
    return Response(ps_script, mimetype="text/plain")

@app.route("/api/stream")
def sse_stream():
    def event_generator():
        # Start by reading the last 20 lines of the log
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, "r", errors="ignore") as f:
                lines = f.readlines()
                for l in lines[-20:]:
                    yield f"data: {l.strip()}\n\n"

        # Continuous file tailing
        with open(LOG_FILE, "r", errors="ignore") as f:
            f.seek(0, os.SEEK_END)
            while True:
                line = f.readline()
                if line:
                    yield f"data: {line.strip()}\n\n"
                else:
                    time.sleep(0.2)

    return Response(event_generator(), mimetype="text/event-stream")

@app.route("/api/trigger", methods=["POST"])
def trigger_test():
    test_type = request.args.get("type", "sqli")
    
    try:
        target_url = get_esp32_url()
        target_ip = get_esp32_ip()
        if test_type == "sqli":
            data = urllib.parse.urlencode({"user": "admin' UNION SELECT 1,2,3--", "pass": "hack"}).encode()
            req = urllib.request.Request(f"{target_url}/login", data=data, headers={"User-Agent": "A-HIDS-TestUI"})
            urllib.request.urlopen(req, timeout=3)
        elif test_type == "canary":
            req = urllib.request.Request(f"{target_url}/.env", headers={"User-Agent": "A-HIDS-TestUI"})
            urllib.request.urlopen(req, timeout=3)
        elif test_type == "telnet":
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2.0)
            s.connect((target_ip, 23))
            s.recv(1024)
            s.close()
        elif test_type == "entropy":
            import base64
            token = base64.b64encode(os.urandom(32)).decode()
            data = urllib.parse.urlencode({"user": token, "pass": "hack"}).encode()
            req = urllib.request.Request(f"{target_url}/login", data=data, headers={"User-Agent": "A-HIDS-TestUI"})
            urllib.request.urlopen(req, timeout=3)

        return jsonify({"status": "fired", "type": test_type})
    except Exception as e:
        return jsonify({"status": "dispatched", "error_or_lockdown": str(e)[:40]})

if __name__ == "__main__":
    print(f"\n====================================================")
    print(f"  A-HIDS TACTICAL C2 WEB DASHBOARD (ANTI-SLOP)")
    print(f"====================================================")
    print(f" Dashboard URL : http://localhost:{PORT}")
    print(f" Forensic Log  : {LOG_FILE}")
    print(f" Target Bait   : {get_esp32_url()}")
    print(f"----------------------------------------------------\n")
    app.run(host="0.0.0.0", port=PORT, threaded=True)
