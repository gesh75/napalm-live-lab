/* ═══════════════════════════════════════════════════════════════════════════
   NAPALM Network Dashboard — Frontend JavaScript
   ═══════════════════════════════════════════════════════════════════════════ */

const API = "";  // same origin
let currentSite = "all";
let currentTool = null;
let currentJobId = null;
let pollTimer = null;
let sitesData = {};

// ── Initialization ─────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", async () => {
    await checkHealth();
    await loadSites();
});

async function checkHealth() {
    try {
        const r = await fetch(`${API}/api/health`);
        const d = await r.json();
        document.getElementById("statusDot").style.background =
            d.status === "ok" ? "var(--accent-green)" : "var(--accent-red)";
        document.getElementById("statusText").textContent =
            `${d.total_devices} devices · ${d.sites.length} sites`;
    } catch (e) {
        document.getElementById("statusDot").style.background = "var(--accent-red)";
        document.getElementById("statusText").textContent = "Disconnected";
    }
}

async function loadSites() {
    try {
        const r = await fetch(`${API}/api/sites`);
        sitesData = await r.json();
        renderSiteSelector();
    } catch (e) {
        console.error("Failed to load sites:", e);
    }
}

function renderSiteSelector() {
    const el = document.getElementById("siteSelector");
    const total = Object.values(sitesData).reduce((s, v) => s + v.device_count, 0);
    document.getElementById("allCount").textContent = total;

    let html = `<button class="site-btn active" data-site="all" onclick="selectSite('all')">
        All Sites <span class="count">${total}</span>
    </button>`;
    for (const [site, info] of Object.entries(sitesData)) {
        html += `<button class="site-btn" data-site="${site}" onclick="selectSite('${site}')">
            ${site.toUpperCase()} <span class="count">${info.device_count}</span>
        </button>`;
    }
    el.innerHTML = html;
}

// ── Site & Tool Selection ──────────────────────────────────────────────────

function selectSite(site) {
    currentSite = site;
    document.querySelectorAll(".site-btn").forEach(b => {
        b.classList.toggle("active", b.dataset.site === site);
    });
    if (currentTool) selectTool(currentTool);
}

function selectTool(tool) {
    currentTool = tool;
    document.querySelectorAll(".tool-btn").forEach(b => {
        b.classList.toggle("active", b.dataset.tool === tool);
    });
    document.getElementById("welcomeView").style.display = "none";
    document.getElementById("toolView").style.display = "block";
    renderToolView(tool);
}

// ── Tool View Rendering ────────────────────────────────────────────────────

const TOOL_META = {
    "version-audit": {
        icon: "⬡", title: "Software Version Audit",
        desc: "Check OS versions across all devices. Detect mismatches within same model/platform.",
        color: "var(--accent-blue)", needsSite: false,
    },
    "bgp-status": {
        icon: "⇋", title: "BGP Peer Status",
        desc: "Live BGP neighbor table — peer state, prefix counts, uptime.",
        color: "var(--accent-green)", needsSite: true,
    },
    "netbox-audit": {
        icon: "⊕", title: "NetBox vs Live Audit",
        desc: "Compare live IP interfaces against NetBox prefixes. Find undocumented or missing subnets.",
        color: "var(--accent-purple)", needsSite: true,
    },
    "env-health": {
        icon: "♥", title: "Environment Health",
        desc: "CPU, memory, temperature, fans, power supply status across all devices.",
        color: "var(--accent-red)", needsSite: true,
    },
    "interface-errors": {
        icon: "⚡", title: "Interface Error Monitor",
        desc: "Find interfaces with CRC errors, discards, and drops. Sorted by severity.",
        color: "var(--accent-orange)", needsSite: true,
    },
    "lldp-topology": {
        icon: "◎", title: "LLDP Topology Map",
        desc: "Discover physical topology via LLDP neighbors. Validate cabling.",
        color: "var(--accent-cyan)", needsSite: true,
    },
    "site-collect": {
        icon: "⬇", title: "Full Site Collection",
        desc: "Collect all data: interfaces, IPs, LLDP, ARP, counters, environment. Saves JSON + Markdown.",
        color: "var(--accent-blue)", needsSite: true,
    },
    "snapshot": {
        icon: "◫", title: "Pre/Post Change Diff",
        desc: "Take snapshots before and after maintenance. Compare interface state, BGP, and IPs.",
        color: "var(--accent-pink)", needsSite: true,
    },
};

function renderToolView(tool) {
    const meta = TOOL_META[tool];
    const tv = document.getElementById("toolView");
    const siteLabel = currentSite === "all" ? "All Sites" : currentSite.toUpperCase();

    const needsSiteWarning = meta.needsSite && currentSite === "all"
        ? `<div class="card" style="border-color: var(--accent-orange);">
             <p style="color: var(--accent-orange);">⚠ Please select a specific site from the sidebar to run this tool.</p>
           </div>`
        : "";

    const canRun = !meta.needsSite || currentSite !== "all";

    if (tool === "snapshot") {
        renderSnapshotView(tv, meta, siteLabel, canRun, needsSiteWarning);
        return;
    }

    tv.innerHTML = `
        <div class="tool-header">
            <h2><span style="color:${meta.color}">${meta.icon}</span> ${meta.title}</h2>
            <div class="tool-header-actions">
                <span style="color: var(--text-secondary); font-size: 13px; padding: 8px;">
                    Site: <strong>${siteLabel}</strong>
                </span>
                <button class="btn btn-primary" id="runBtn" onclick="runTool('${tool}')"
                    ${canRun ? "" : "disabled"}>
                    ▶ Run ${meta.title}
                </button>
            </div>
        </div>
        <p style="color: var(--text-secondary); margin-bottom: 16px;">${meta.desc}</p>
        ${needsSiteWarning}
        <div id="progressArea" style="display:none;">
            <div class="progress-text">
                <span class="spinner"></span>
                <span id="progressMsg">Starting...</span>
            </div>
            <div class="progress-bar"><div class="progress-fill" id="progressFill" style="width:0%"></div></div>
        </div>
        <div id="resultArea"></div>
    `;
}

function renderSnapshotView(tv, meta, siteLabel, canRun, needsSiteWarning) {
    tv.innerHTML = `
        <div class="tool-header">
            <h2><span style="color:${meta.color}">${meta.icon}</span> ${meta.title}</h2>
            <div class="tool-header-actions">
                <span style="color: var(--text-secondary); font-size: 13px; padding: 8px;">
                    Site: <strong>${siteLabel}</strong>
                </span>
            </div>
        </div>
        <p style="color: var(--text-secondary); margin-bottom: 16px;">${meta.desc}</p>
        ${needsSiteWarning}

        <div class="stats-grid" style="grid-template-columns: 1fr 1fr; margin-bottom: 20px;">
            <div class="card" style="text-align: center;">
                <h3 style="margin-bottom: 12px; color: var(--accent-green);">① PRE-Change Snapshot</h3>
                <p style="color: var(--text-secondary); font-size: 13px; margin-bottom: 12px;">
                    Take before starting maintenance
                </p>
                <button class="btn btn-success" onclick="takeSnapshot('pre')" ${canRun ? "" : "disabled"}>
                    📸 Take PRE Snapshot
                </button>
            </div>
            <div class="card" style="text-align: center;">
                <h3 style="margin-bottom: 12px; color: var(--accent-orange);">② POST-Change Snapshot</h3>
                <p style="color: var(--text-secondary); font-size: 13px; margin-bottom: 12px;">
                    Take after completing maintenance
                </p>
                <button class="btn btn-danger" onclick="takeSnapshot('post')" ${canRun ? "" : "disabled"}>
                    📸 Take POST Snapshot
                </button>
            </div>
        </div>

        <div id="progressArea" style="display:none;">
            <div class="progress-text">
                <span class="spinner"></span>
                <span id="progressMsg">Starting...</span>
            </div>
            <div class="progress-bar"><div class="progress-fill" id="progressFill" style="width:0%"></div></div>
        </div>

        <div class="card">
            <div class="card-title">③ Compare Snapshots</div>
            <div class="snap-controls">
                <div>
                    <label>PRE Snapshot (Before)</label>
                    <select id="snapA" style="min-width: 280px;"><option value="">Loading...</option></select>
                </div>
                <div>
                    <label>POST Snapshot (After)</label>
                    <select id="snapB" style="min-width: 280px;"><option value="">Loading...</option></select>
                </div>
                <button class="btn btn-primary" onclick="runDiff()">🔍 Compare</button>
            </div>
        </div>

        <div id="resultArea"></div>
    `;
    if (canRun) loadSnapshotList();
}

async function loadSnapshotList() {
    const site = currentSite.toUpperCase();
    try {
        const r = await fetch(`${API}/api/tools/snapshots/${site}`);
        const snaps = await r.json();
        const selA = document.getElementById("snapA");
        const selB = document.getElementById("snapB");
        if (!selA || !selB) return;

        const opts = snaps.map(s =>
            `<option value="${s.file}">${s.file} (${formatBytes(s.size)})</option>`
        ).join("");
        selA.innerHTML = opts || '<option value="">No snapshots found</option>';
        selB.innerHTML = opts || '<option value="">No snapshots found</option>';
        if (snaps.length >= 2) selB.selectedIndex = 1;
    } catch (e) {
        console.error("Failed to load snapshots:", e);
    }
}

// ── Run Tools ──────────────────────────────────────────────────────────────

async function runTool(tool) {
    const site = currentSite;
    const btn = document.getElementById("runBtn");
    if (btn) btn.disabled = true;
    showProgress("Starting...", 0);

    try {
        const r = await fetch(`${API}/api/tools/${tool}`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({site}),
        });
        const d = await r.json();
        if (d.error) {
            hideProgress();
            showError(d.error);
            if (btn) btn.disabled = false;
            return;
        }
        currentJobId = d.job_id;
        pollJob(d.job_id, tool);
    } catch (e) {
        hideProgress();
        showError(e.message);
        if (btn) btn.disabled = false;
    }
}

async function takeSnapshot(label) {
    showProgress(`Taking ${label.toUpperCase()} snapshot...`, 0);
    try {
        const r = await fetch(`${API}/api/tools/snapshot`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({site: currentSite, label}),
        });
        const d = await r.json();
        currentJobId = d.job_id;
        pollJob(d.job_id, "snapshot");
    } catch (e) {
        hideProgress();
        showError(e.message);
    }
}

async function runDiff() {
    const fileA = document.getElementById("snapA")?.value;
    const fileB = document.getElementById("snapB")?.value;
    if (!fileA || !fileB) { showError("Select two snapshots"); return; }

    document.getElementById("resultArea").innerHTML =
        '<div class="progress-text"><span class="spinner"></span> Comparing...</div>';

    try {
        const r = await fetch(`${API}/api/tools/snapshot-diff`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({file_a: fileA, file_b: fileB}),
        });
        const d = await r.json();
        renderDiffResult(d);
    } catch (e) {
        showError(e.message);
    }
}

// ── Job Polling ────────────────────────────────────────────────────────────

function pollJob(jobId, tool) {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(async () => {
        try {
            const r = await fetch(`${API}/api/jobs/${jobId}`);
            const job = await r.json();

            showProgress(job.message, job.progress);

            if (job.status === "done") {
                clearInterval(pollTimer);
                hideProgress();
                const btn = document.getElementById("runBtn");
                if (btn) btn.disabled = false;
                renderResult(tool, job.result);
                updateRecentJobs();
            } else if (job.status === "error") {
                clearInterval(pollTimer);
                hideProgress();
                const btn = document.getElementById("runBtn");
                if (btn) btn.disabled = false;
                showError(job.message);
            }
        } catch (e) {
            console.error("Poll error:", e);
        }
    }, 1000);
}

// ── Progress ───────────────────────────────────────────────────────────────

function showProgress(msg, pct) {
    const area = document.getElementById("progressArea");
    if (area) area.style.display = "block";
    const pmsg = document.getElementById("progressMsg");
    if (pmsg) pmsg.textContent = msg;
    const fill = document.getElementById("progressFill");
    if (fill) fill.style.width = pct + "%";
}

function hideProgress() {
    const area = document.getElementById("progressArea");
    if (area) area.style.display = "none";
}

function showError(msg) {
    const ra = document.getElementById("resultArea");
    if (ra) ra.innerHTML = `<div class="alert-item alert-critical">
        <span class="alert-icon">❌</span>
        <span class="alert-text">${escapeHtml(msg)}</span>
    </div>`;
}

// ── Result Renderers ───────────────────────────────────────────────────────

function renderResult(tool, data) {
    const renderers = {
        "version-audit": renderVersionAudit,
        "bgp-status": renderBgpStatus,
        "netbox-audit": renderNetboxAudit,
        "env-health": renderEnvHealth,
        "interface-errors": renderInterfaceErrors,
        "lldp-topology": renderLldpTopology,
        "site-collect": renderSiteCollect,
        "snapshot": renderSnapshotResult,
    };
    const fn = renderers[tool];
    if (fn) fn(data);
    else document.getElementById("resultArea").innerHTML = `<pre>${JSON.stringify(data, null, 2)}</pre>`;
}

// ── 1. Version Audit ───────────────────────────────────────────────────────

function renderVersionAudit(data) {
    const ra = document.getElementById("resultArea");
    let html = `
        <div class="stats-grid">
            <div class="stat-card stat-blue"><div class="stat-value">${data.total}</div><div class="stat-label">Total Devices</div></div>
            <div class="stat-card stat-green"><div class="stat-value">${data.total - data.errors}</div><div class="stat-label">Reachable</div></div>
            <div class="stat-card stat-red"><div class="stat-value">${data.errors}</div><div class="stat-label">Unreachable</div></div>
            <div class="stat-card ${data.mismatches.length ? 'stat-orange' : 'stat-green'}">
                <div class="stat-value">${data.mismatches.length}</div><div class="stat-label">Version Mismatches</div>
            </div>
        </div>
    `;

    if (data.mismatches.length) {
        html += `<div class="card"><div class="card-title">⚠️ Version Mismatches</div>`;
        for (const m of data.mismatches) {
            html += `<div class="alert-item alert-warning">
                <span class="alert-icon">⚠️</span>
                <span class="alert-text">
                    <strong>${m.model}</strong> (${m.driver}) has ${m.versions.length} different versions:
                    ${m.versions.map(v => `<span class="badge badge-warn">${v}</span>`).join(" ")}
                    <br><small style="color:var(--text-muted)">${m.devices.join(", ")}</small>
                </span>
            </div>`;
        }
        html += `</div>`;
    }

    html += `<div class="card"><div class="card-title">📋 Device Inventory</div><table>
        <tr><th>Site</th><th>Device</th><th>IP</th><th>Vendor</th><th>Model</th><th>OS Version</th><th>Serial</th><th>Uptime</th><th>Status</th></tr>`;
    for (const d of data.devices) {
        const status = d.error
            ? `<span class="badge badge-error">Error</span>`
            : `<span class="badge badge-ok">OK</span>`;
        html += `<tr>
            <td>${d.site}</td>
            <td class="mono">${d.hostname}</td>
            <td class="mono">${d.ip}</td>
            <td>${d.vendor}</td>
            <td>${d.model}</td>
            <td class="mono">${d.os_version}</td>
            <td class="mono">${d.serial}</td>
            <td>${formatUptime(d.uptime)}</td>
            <td>${status}</td>
        </tr>`;
    }
    html += `</table></div>`;
    ra.innerHTML = html;
}

// ── 2. BGP Status ──────────────────────────────────────────────────────────

function renderBgpStatus(data) {
    const ra = document.getElementById("resultArea");
    let html = `
        <div class="stats-grid">
            <div class="stat-card stat-blue"><div class="stat-value">${data.devices.length}</div><div class="stat-label">Devices</div></div>
            <div class="stat-card stat-green"><div class="stat-value">${data.total_peers}</div><div class="stat-label">Total Peers</div></div>
            <div class="stat-card stat-green"><div class="stat-value">${data.total_peers - data.total_down}</div><div class="stat-label">Peers Up</div></div>
            <div class="stat-card ${data.total_down ? 'stat-red' : 'stat-green'}">
                <div class="stat-value">${data.total_down}</div><div class="stat-label">Peers Down</div>
            </div>
        </div>
    `;

    // Alerts for down peers
    const downPeers = [];
    for (const dev of data.devices) {
        for (const p of (dev.peers || [])) {
            if (!p.is_up) downPeers.push({hostname: dev.hostname, ...p});
        }
    }
    if (downPeers.length) {
        html += `<div class="card"><div class="card-title">🔴 Down BGP Peers</div>`;
        for (const p of downPeers) {
            html += `<div class="alert-item alert-critical">
                <span class="alert-icon">❌</span>
                <span class="alert-text">
                    <span class="alert-host">${p.hostname}</span>
                    Peer <strong class="mono">${p.peer_ip}</strong>
                    ${p.description ? `(${p.description})` : ""} — VRF: ${p.vrf}
                </span>
            </div>`;
        }
        html += `</div>`;
    }

    for (const dev of data.devices) {
        if (dev.error) {
            html += `<div class="card"><div class="card-title">${dev.hostname} — <span class="badge badge-error">Error: ${dev.error}</span></div></div>`;
            continue;
        }
        if (!dev.peers.length) continue;

        html += `<div class="card"><div class="card-title">${dev.hostname} — ${dev.peers.length} peers (${dev.up} up, ${dev.down} down)</div>
        <table><tr><th>Peer IP</th><th>VRF</th><th>State</th><th>Description</th><th>Uptime</th><th>Rcvd</th><th>Sent</th></tr>`;
        for (const p of dev.peers) {
            const badge = p.is_up ? '<span class="badge badge-up">UP</span>' : '<span class="badge badge-down">DOWN</span>';
            html += `<tr>
                <td class="mono">${p.peer_ip}</td><td>${p.vrf}</td><td>${badge}</td>
                <td>${p.description || "-"}</td><td>${formatUptime(p.uptime)}</td>
                <td>${p.received}</td><td>${p.sent}</td>
            </tr>`;
        }
        html += `</table></div>`;
    }
    ra.innerHTML = html;
}

// ── 3. NetBox Audit ────────────────────────────────────────────────────────

function renderNetboxAudit(data) {
    const ra = document.getElementById("resultArea");
    let html = `
        <div class="stats-grid">
            <div class="stat-card stat-blue"><div class="stat-value">${data.devices.length}</div><div class="stat-label">Devices Scanned</div></div>
            <div class="stat-card stat-purple"><div class="stat-value">${data.netbox_prefixes}</div><div class="stat-label">NetBox Prefixes</div></div>
            <div class="stat-card stat-green"><div class="stat-value">${data.matched.length}</div><div class="stat-label">Matched</div></div>
            <div class="stat-card ${data.live_only.length ? 'stat-red' : 'stat-green'}">
                <div class="stat-value">${data.live_only.length}</div><div class="stat-label">Live Only (Missing)</div>
            </div>
            <div class="stat-card ${data.netbox_only.length ? 'stat-orange' : 'stat-green'}">
                <div class="stat-value">${data.netbox_only.length}</div><div class="stat-label">NetBox Only</div>
            </div>
        </div>
    `;

    if (data.live_only.length) {
        html += `<div class="card"><div class="card-title">🔴 Live Subnets Missing from NetBox</div>`;
        for (const p of data.live_only) {
            html += `<div class="alert-item alert-critical">
                <span class="alert-icon">⚠️</span>
                <span class="alert-text"><span class="mono">${p}</span> — Active on devices but <strong>not documented</strong> in NetBox</span>
            </div>`;
        }
        html += `</div>`;
    }

    if (data.netbox_only.length) {
        html += `<div class="card"><div class="card-title">⚠️ NetBox Prefixes Not Seen Live</div>`;
        for (const p of data.netbox_only) {
            html += `<div class="alert-item alert-warning">
                <span class="alert-icon">📋</span>
                <span class="alert-text"><span class="mono">${p}</span> — In NetBox but not seen on any live interface</span>
            </div>`;
        }
        html += `</div>`;
    }

    html += `<div class="card"><div class="card-title">✅ Matched Prefixes (${data.matched.length})</div>
        <table><tr><th>Prefix</th></tr>`;
    for (const p of data.matched) {
        html += `<tr><td class="mono">${p}</td></tr>`;
    }
    html += `</table></div>`;

    html += `<div class="card"><div class="card-title">📋 Device Summary</div><table>
        <tr><th>Device</th><th>IP</th><th>Driver</th><th>Model</th><th>Interfaces</th><th>IPs</th><th>Status</th></tr>`;
    for (const d of data.devices) {
        const status = d.error ? `<span class="badge badge-error">${d.error}</span>` : `<span class="badge badge-ok">OK</span>`;
        html += `<tr><td class="mono">${d.hostname}</td><td class="mono">${d.ip}</td><td>${d.driver}</td>
            <td>${d.model}</td><td>${d.interfaces}</td><td>${d.ips}</td><td>${status}</td></tr>`;
    }
    html += `</table></div>`;
    ra.innerHTML = html;
}

// ── 4. Environment Health ──────────────────────────────────────────────────

function renderEnvHealth(data) {
    const ra = document.getElementById("resultArea");
    const alertCount = data.alerts.length;
    const critCount = data.alerts.filter(a => a.critical).length;

    let html = `
        <div class="stats-grid">
            <div class="stat-card stat-blue"><div class="stat-value">${data.devices.length}</div><div class="stat-label">Devices</div></div>
            <div class="stat-card ${alertCount ? 'stat-red' : 'stat-green'}">
                <div class="stat-value">${alertCount}</div><div class="stat-label">Total Alerts</div>
            </div>
            <div class="stat-card ${critCount ? 'stat-red' : 'stat-green'}">
                <div class="stat-value">${critCount}</div><div class="stat-label">Critical</div>
            </div>
        </div>
    `;

    if (data.alerts.length) {
        html += `<div class="card"><div class="card-title">🚨 Health Alerts</div>`;
        for (const a of data.alerts) {
            const cls = a.critical ? "alert-critical" : "alert-warning";
            const icon = a.critical ? "🔴" : "⚠️";
            html += `<div class="alert-item ${cls}">
                <span class="alert-icon">${icon}</span>
                <span class="alert-text">
                    <span class="alert-host">${a.hostname}</span>
                    ${a.type.toUpperCase()}: ${a.sensor} = <strong>${a.value}</strong>
                </span>
            </div>`;
        }
        html += `</div>`;
    }

    html += `<div class="card"><div class="card-title">📊 Device Health Overview</div><table>
        <tr><th>Device</th><th>Model</th><th>CPU %</th><th>Memory %</th><th>Fans</th><th>Power</th><th>Temp Alerts</th><th>Uptime</th></tr>`;
    for (const d of data.devices) {
        if (d.error) {
            html += `<tr><td class="mono">${d.hostname}</td><td colspan="7"><span class="badge badge-error">${d.error}</span></td></tr>`;
            continue;
        }
        const cpuBadge = d.cpu_pct > 80 ? "badge-error" : d.cpu_pct > 60 ? "badge-warn" : "badge-ok";
        const memBadge = d.memory_pct > 85 ? "badge-error" : d.memory_pct > 70 ? "badge-warn" : "badge-ok";
        html += `<tr>
            <td class="mono">${d.hostname}</td><td>${d.model}</td>
            <td><span class="badge ${cpuBadge}">${d.cpu_pct}%</span></td>
            <td><span class="badge ${memBadge}">${d.memory_pct}%</span></td>
            <td>${d.fans_ok ? '<span class="badge badge-ok">OK</span>' : '<span class="badge badge-error">FAIL</span>'}</td>
            <td>${d.power_ok ? '<span class="badge badge-ok">OK</span>' : '<span class="badge badge-error">FAIL</span>'}</td>
            <td>${d.temp_alerts.length ? `<span class="badge badge-error">${d.temp_alerts.length}</span>` : '<span class="badge badge-ok">OK</span>'}</td>
            <td>${formatUptime(d.uptime)}</td>
        </tr>`;
    }
    html += `</table></div>`;
    ra.innerHTML = html;
}

// ── 5. Interface Errors ────────────────────────────────────────────────────

function renderInterfaceErrors(data) {
    const ra = document.getElementById("resultArea");
    let html = `
        <div class="stats-grid">
            <div class="stat-card ${data.total_interfaces_with_errors ? 'stat-orange' : 'stat-green'}">
                <div class="stat-value">${data.total_interfaces_with_errors}</div>
                <div class="stat-label">Interfaces with Errors</div>
            </div>
        </div>
    `;

    if (!data.errors.length) {
        html += `<div class="empty-state"><div class="empty-state-icon">✅</div>
            <p>No interface errors found — all clean!</p></div>`;
        ra.innerHTML = html;
        return;
    }

    html += `<div class="card"><div class="card-title">⚡ Interface Errors (sorted by total)</div><table>
        <tr><th>Device</th><th>Interface</th><th>Description</th><th>State</th><th>Speed</th>
            <th>RX Errors</th><th>TX Errors</th><th>RX Discards</th><th>TX Discards</th><th>Total</th></tr>`;
    for (const e of data.errors) {
        const state = e.is_up ? '<span class="badge badge-up">UP</span>' : '<span class="badge badge-down">DOWN</span>';
        const severity = e.total > 10000 ? "badge-error" : e.total > 1000 ? "badge-warn" : "badge-info";
        html += `<tr>
            <td class="mono">${e.hostname}</td><td class="mono">${e.interface}</td>
            <td>${e.description || "-"}</td><td>${state}</td><td>${e.speed || "-"}</td>
            <td>${fmtNum(e.rx_errors)}</td><td>${fmtNum(e.tx_errors)}</td>
            <td>${fmtNum(e.rx_discards)}</td><td>${fmtNum(e.tx_discards)}</td>
            <td><span class="badge ${severity}">${fmtNum(e.total)}</span></td>
        </tr>`;
    }
    html += `</table></div>`;
    ra.innerHTML = html;
}

// ── 6. LLDP Topology ──────────────────────────────────────────────────────

function renderLldpTopology(data) {
    const ra = document.getElementById("resultArea");
    let html = `
        <div class="stats-grid">
            <div class="stat-card stat-cyan"><div class="stat-value">${data.total_nodes}</div><div class="stat-label">Nodes Discovered</div></div>
            <div class="stat-card stat-blue"><div class="stat-value">${data.total_links}</div><div class="stat-label">Unique Links</div></div>
        </div>
    `;

    // Links table
    html += `<div class="card"><div class="card-title">🔗 LLDP Links</div><table>
        <tr><th>Source Device</th><th>Source Port</th><th>→</th><th>Target Device</th><th>Target Port</th></tr>`;
    for (const l of data.links) {
        html += `<tr>
            <td class="mono">${l.source}</td><td class="mono">${l.source_port}</td>
            <td style="color:var(--accent-cyan)">→</td>
            <td class="mono">${l.target}</td><td class="mono">${l.target_port}</td>
        </tr>`;
    }
    html += `</table></div>`;

    // Node grid
    const nodeLinks = {};
    for (const l of data.links) {
        if (!nodeLinks[l.source]) nodeLinks[l.source] = [];
        nodeLinks[l.source].push(l);
    }

    html += `<div class="card"><div class="card-title">◎ Topology Nodes</div><div class="topo-grid">`;
    for (const node of data.nodes) {
        const links = nodeLinks[node] || [];
        html += `<div class="topo-node">
            <div class="topo-node-title">${node}</div>`;
        if (links.length) {
            for (const l of links) {
                html += `<div class="topo-link">
                    <span class="topo-port">${l.source_port}</span>
                    <span style="color:var(--text-muted)">→</span>
                    <span>${l.target}</span>
                    <span class="topo-port">${l.target_port}</span>
                </div>`;
            }
        } else {
            html += `<div style="color:var(--text-muted); font-size:12px;">leaf node (no outgoing LLDP)</div>`;
        }
        html += `</div>`;
    }
    html += `</div></div>`;
    ra.innerHTML = html;
}

// ── 7. Site Collect ────────────────────────────────────────────────────────

function renderSiteCollect(data) {
    const ra = document.getElementById("resultArea");
    let html = `
        <div class="stats-grid">
            <div class="stat-card stat-blue"><div class="stat-value">${data.devices.length}</div><div class="stat-label">Devices Collected</div></div>
        </div>
        <div class="alert-item alert-info">
            <span class="alert-icon">📁</span>
            <span class="alert-text">Saved to: <strong class="mono">${data.output_file}</strong></span>
        </div>
        <div class="card"><div class="card-title">📊 Collection Summary</div><table>
        <tr><th>Device</th><th>Model</th><th>Version</th><th>Interfaces</th><th>IPs</th><th>LLDP</th><th>ARP</th><th>Status</th></tr>
    `;
    for (const d of data.devices) {
        const status = d.error ? `<span class="badge badge-error">${d.error}</span>` : `<span class="badge badge-ok">OK</span>`;
        html += `<tr>
            <td class="mono">${d.hostname}</td><td>${d.model}</td><td class="mono">${d.version}</td>
            <td>${d.interfaces}</td><td>${d.ips}</td><td>${d.lldp_neighbors}</td><td>${d.arp_entries}</td>
            <td>${status}</td>
        </tr>`;
    }
    html += `</table></div>`;
    ra.innerHTML = html;
}

// ── 8. Snapshot Result ─────────────────────────────────────────────────────

function renderSnapshotResult(data) {
    const ra = document.getElementById("resultArea");
    ra.innerHTML = `
        <div class="alert-item alert-info">
            <span class="alert-icon">📸</span>
            <span class="alert-text">
                <strong>${data.label.toUpperCase()}</strong> snapshot saved:
                <strong class="mono">${data.file}</strong>
                (${data.devices} devices)
            </span>
        </div>
    `;
    loadSnapshotList();
}

function renderDiffResult(data) {
    const ra = document.getElementById("resultArea");
    let html = `
        <div class="stats-grid">
            <div class="stat-card ${data.total_changes ? 'stat-orange' : 'stat-green'}">
                <div class="stat-value">${data.total_changes}</div>
                <div class="stat-label">Changes Detected</div>
            </div>
        </div>
    `;

    if (!data.changes.length) {
        html += `<div class="empty-state"><div class="empty-state-icon">✅</div>
            <p>No changes detected between snapshots — network state is identical!</p></div>`;
        ra.innerHTML = html;
        return;
    }

    html += `<div class="card"><div class="card-title">🔍 Changes (${data.file_a} → ${data.file_b})</div>`;
    for (const c of data.changes) {
        const cls = c.type.includes("removed") ? "diff-remove"
            : c.type.includes("added") ? "diff-add" : "diff-change";
        const icon = c.type.includes("removed") ? "➖"
            : c.type.includes("added") ? "➕" : "🔄";
        html += `<div class="diff-item ${cls}">
            <span>${icon}</span>
            <span class="alert-host">${c.hostname}</span>
            <span class="badge badge-info">${c.type}</span>
            ${c.interface ? `<span class="mono">${c.interface}</span>` : ""}
            <span>${c.details}</span>
        </div>`;
    }
    html += `</div>`;
    ra.innerHTML = html;
}

// ── Recent Jobs ────────────────────────────────────────────────────────────

async function updateRecentJobs() {
    try {
        const r = await fetch(`${API}/api/jobs`);
        const jobs = await r.json();
        const el = document.getElementById("recentJobs");
        if (!jobs.length) { el.innerHTML = "No jobs yet"; return; }

        el.innerHTML = jobs.slice(-5).reverse().map(j => {
            const icon = j.status === "done" ? "✅" : j.status === "error" ? "❌" : "⏳";
            return `<div style="padding: 4px 0; border-bottom: 1px solid var(--border);">
                ${icon} <strong>${j.type}</strong> ${j.site.toUpperCase()}<br>
                <small style="color:var(--text-muted)">${j.message}</small>
            </div>`;
        }).join("");
    } catch (e) {}
}

// ── Utilities ──────────────────────────────────────────────────────────────

function escapeHtml(s) {
    return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}

function formatUptime(seconds) {
    if (!seconds || seconds < 0) return "-";
    const d = Math.floor(seconds / 86400);
    const h = Math.floor((seconds % 86400) / 3600);
    if (d > 0) return `${d}d ${h}h`;
    const m = Math.floor((seconds % 3600) / 60);
    return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

function formatBytes(bytes) {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1048576) return (bytes / 1024).toFixed(1) + " KB";
    return (bytes / 1048576).toFixed(1) + " MB";
}

function fmtNum(n) {
    return (n || 0).toLocaleString();
}
