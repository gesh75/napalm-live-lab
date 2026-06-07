/* ============================================================================
 * lab.js — client-side logic for the live-lab dashboard (lab.html)
 * Served at http://127.0.0.1:5959/lab — vanilla JS, no build step, no deps.
 *
 * Consumes the Flask API:
 *   GET /api/lab/matrix?fabric=clos|dcn|all   -> reachability + getter matrix
 *   GET /api/lab/topology?fabric=clos|dcn      -> nodes + links for SVG draw
 * (GET /api/lab/fabrics is available but the toggle buttons are static in HTML.)
 *
 * Design goals: defensive (never throw on missing fields), small helpers,
 * readable auto-sizing SVG topology, GitHub-dark palette reused exactly.
 * ========================================================================== */

(function () {
  "use strict";

  // ---- Constants -----------------------------------------------------------
  const API = ""; // same origin (http://127.0.0.1:5959)
  const REFRESH_MS = 15000;
  const GETTERS = [
    "get_facts",
    "get_interfaces",
    "get_interfaces_ip",
    "get_bgp_neighbors",
    "get_lldp_neighbors",
    "get_environment",
  ];
  const VENDOR_COLOR = { arista: "#d29922", nokia: "#39d2c0", frr: "#bc8cff" };
  const C = {
    green: "#3fb950",
    red: "#f85149",
    orange: "#d29922",
    blue: "#58a6ff",
    border: "#30363d",
    text: "#e6edf3",
    muted: "#6e7681",
    sub: "#8b949e",
  };

  // ---- State ---------------------------------------------------------------
  let currentFabric = "all";
  let autoTimer = null;

  // ---- Tiny DOM helpers ----------------------------------------------------
  /** Create an element with attrs + children. children may be nodes/strings. */
  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    if (attrs) {
      for (const k in attrs) {
        if (k === "style" && typeof attrs[k] === "object") {
          Object.assign(node.style, attrs[k]);
        } else if (k === "text") {
          // Always plain text — never innerHTML — so user/API data can't inject markup.
          node.textContent = attrs[k];
        } else if (attrs[k] != null) {
          node.setAttribute(k, attrs[k]);
        }
      }
    }
    appendChildren(node, children);
    return node;
  }

  function appendChildren(node, children) {
    if (children == null) return;
    const list = Array.isArray(children) ? children : [children];
    for (const c of list) {
      if (c == null) continue;
      node.appendChild(typeof c === "object" ? c : document.createTextNode(String(c)));
    }
  }

  /** A small colored pill (used for vendor labels). */
  function chip(label, color) {
    return el("span", {
      class: "lab-chip",
      style: {
        display: "inline-block",
        padding: "1px 8px",
        borderRadius: "10px",
        fontSize: "11px",
        fontWeight: "600",
        color: color || C.text,
        border: "1px solid " + (color || C.border),
        background: hexA(color || C.border, 0.12),
      },
      text: label,
    });
  }

  /** A method badge: napalm=green, exec=orange, anything else=muted. */
  function badge(method) {
    const m = (method || "").toLowerCase();
    const color = m === "napalm" ? C.green : m === "exec" ? C.orange : C.muted;
    return el("span", {
      class: "lab-badge",
      style: {
        display: "inline-block",
        padding: "1px 7px",
        borderRadius: "4px",
        fontSize: "11px",
        fontWeight: "600",
        color: color,
        border: "1px solid " + hexA(color, 0.5),
        background: hexA(color, 0.12),
      },
      text: m || "—",
    });
  }

  /** A small status dot. */
  function dot(ok) {
    return el("span", {
      title: ok ? "reachable" : "unreachable",
      style: {
        display: "inline-block",
        width: "10px",
        height: "10px",
        borderRadius: "50%",
        background: ok ? C.green : C.red,
      },
    });
  }

  /** Convert #rrggbb + alpha to rgba() string (defensive on bad input). */
  function hexA(hex, a) {
    if (typeof hex !== "string" || hex[0] !== "#" || hex.length < 7) {
      return "rgba(110,118,129," + a + ")";
    }
    const r = parseInt(hex.slice(1, 3), 16);
    const g = parseInt(hex.slice(3, 5), 16);
    const b = parseInt(hex.slice(5, 7), 16);
    return "rgba(" + r + "," + g + "," + b + "," + a + ")";
  }

  function vendorColor(v) {
    return VENDOR_COLOR[(v || "").toLowerCase()] || C.muted;
  }

  function txt(v, fallback) {
    return v == null || v === "" ? (fallback || "—") : String(v);
  }

  function setText(id, value) {
    const node = document.getElementById(id);
    if (node) node.textContent = value == null ? "—" : String(value);
  }

  // ---- Data fetching -------------------------------------------------------
  /** Fetch JSON defensively; throws on non-ok so callers can show a message. */
  async function getJSON(url) {
    const res = await fetch(API + url, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    if (!res.ok) throw new Error("HTTP " + res.status + " for " + url);
    return res.json();
  }

  // ---- Matrix table --------------------------------------------------------
  /** Fetch the matrix for a fabric and render rows + stat cards. */
  async function fetchMatrix(fabric) {
    const body = document.getElementById("matrixBody");
    if (!body) return;
    try {
      const data = await getJSON("/api/lab/matrix?fabric=" + encodeURIComponent(fabric));
      renderMatrix(data);
    } catch (err) {
      renderMatrixError(body, err);
    }
  }

  function renderMatrixError(body, err) {
    body.innerHTML = "";
    const cols = 9 + GETTERS.length; // base cells + getter cells + latency
    body.appendChild(
      el("tr", null, [
        el("td", {
          colspan: cols,
          style: { padding: "20px", textAlign: "center", color: C.red },
          text: "Could not load matrix: " + (err && err.message ? err.message : "unknown error"),
        }),
      ])
    );
  }

  function renderMatrix(data) {
    const body = document.getElementById("matrixBody");
    if (!body) return;
    body.innerHTML = "";

    const nodes = Array.isArray(data && data.nodes) ? data.nodes : [];
    if (!nodes.length) {
      body.appendChild(
        el("tr", null, [
          el("td", {
            colspan: 9 + GETTERS.length,
            style: { padding: "20px", textAlign: "center", color: C.sub },
            text: "No nodes reported for this fabric.",
          }),
        ])
      );
    } else {
      for (const n of nodes) body.appendChild(matrixRow(n));
    }

    updateStats(data && data.summary, data && data.generated);
  }

  /** Build a single <tr> for one node. */
  function matrixRow(n) {
    n = n || {};
    const reachable = n.reachable === true;
    const method = (n.method || "").toLowerCase();
    const getters = n.getters || {};

    const cells = [
      el("td", { style: { fontFamily: "ui-monospace, monospace", color: C.blue } }, txt(n.hostname)),
      el("td", null, txt(n.fabric)),
      el("td", null, txt(n.tier)),
      el("td", null, chip(txt(n.vendor), vendorColor(n.vendor))),
      el("td", null, txt(n.model)),
      el("td", { style: { fontFamily: "ui-monospace, monospace", color: C.sub } }, txt(n.driver)),
      el("td", null, badge(method)),
      el("td", { style: { textAlign: "center" } }, dot(reachable)),
    ];

    // One cell per getter, in the contract order.
    for (const g of GETTERS) {
      cells.push(getterCell(getters[g], method, reachable));
    }

    // Latency cell.
    const lat = n.latency_ms;
    cells.push(
      el(
        "td",
        { style: { textAlign: "right", color: C.muted, fontFamily: "ui-monospace, monospace" } },
        lat == null ? "—" : lat + "ms"
      )
    );

    return el("tr", null, cells);
  }

  /**
   * Getter status cell:
   *   ✅ if getters[g].ok
   *   ⚠  if method==="exec" and reachable but getter not ok
   *   ❌  if reachable but failed with an error
   *   —  if not reachable
   */
  function getterCell(g, method, reachable) {
    g = g || {};
    let symbol = "—";
    let color = C.muted;
    const error = g.error || null;

    if (!reachable) {
      symbol = "—";
      color = C.muted;
    } else if (g.ok === true) {
      symbol = "✅";
      color = C.green;
    } else if (method === "exec") {
      symbol = "⚠";
      color = C.orange;
    } else if (error) {
      symbol = "❌";
      color = C.red;
    } else {
      // reachable, not ok, no error, not exec -> treat as failure marker
      symbol = "❌";
      color = C.red;
    }

    return el(
      "td",
      {
        style: { textAlign: "center", color: color, cursor: error ? "help" : "default" },
        title: error || (g.ok ? "ok" : ""),
      },
      symbol
    );
  }

  function updateStats(summary, generated) {
    summary = summary || {};
    setText("statTotal", summary.total != null ? summary.total : "—");
    setText("statNapalm", summary.napalm_native != null ? summary.napalm_native : "—");
    setText("statExec", summary.exec_fallback != null ? summary.exec_fallback : "—");
    setText("statReachable", summary.reachable != null ? summary.reachable : "—");
    setText("statGen", generated || "—");
  }

  // ---- Topology (SVG) ------------------------------------------------------
  const SVG_NS = "http://www.w3.org/2000/svg";

  function svgEl(tag, attrs) {
    const node = document.createElementNS(SVG_NS, tag);
    if (attrs) {
      for (const k in attrs) {
        if (attrs[k] != null) node.setAttribute(k, attrs[k]);
      }
    }
    return node;
  }

  /** Show/hide topology containers based on the active fabric. */
  function applyTopologyVisibility(fabric) {
    const closBox = document.getElementById("topoClos");
    const dcnBox = document.getElementById("topoDcn");
    const showClos = fabric === "all" || fabric === "clos";
    const showDcn = fabric === "all" || fabric === "dcn";
    if (closBox) closBox.style.display = showClos ? "" : "none";
    if (dcnBox) dcnBox.style.display = showDcn ? "" : "none";
  }

  /** Fetch a topology and render an SVG into the given container id. */
  async function renderTopology(fabric, elId) {
    const box = document.getElementById(elId);
    if (!box) return;
    box.innerHTML = "";
    try {
      const data = await getJSON("/api/lab/topology?fabric=" + encodeURIComponent(fabric));
      drawTopology(box, data);
    } catch (err) {
      box.appendChild(
        el("div", {
          style: { padding: "16px", color: C.red, fontSize: "13px" },
          text: "Topology unavailable: " + (err && err.message ? err.message : "error"),
        })
      );
    }
  }

  /** Layout + draw nodes/links. CLOS = 2 rows by tier; 3-Tier = N rows. */
  function drawTopology(box, data) {
    data = data || {};
    const nodes = Array.isArray(data.nodes) ? data.nodes : [];
    const links = Array.isArray(data.links) ? data.links : [];
    const tiers = Array.isArray(data.tiers) && data.tiers.length
      ? data.tiers
      : inferTiers(nodes);

    if (!nodes.length) {
      box.appendChild(
        el("div", { style: { padding: "16px", color: C.sub, fontSize: "13px" }, text: "No nodes." })
      );
      return;
    }

    // Sizing.
    const W = Math.max(box.clientWidth || 0, 320);
    const NODE_W = 104;
    const NODE_H = 46;
    const ROW_GAP = 110;
    const TOP_PAD = 50;
    const H = TOP_PAD * 2 + (tiers.length - 1) * ROW_GAP + NODE_H;

    // Group nodes by tier (preserving tier order).
    const byTier = {};
    for (const t of tiers) byTier[t] = [];
    for (const n of nodes) {
      const t = n && n.tier && byTier[n.tier] ? n.tier : tiers[0];
      byTier[t].push(n);
    }

    // Compute positions per node id.
    const pos = {};
    tiers.forEach(function (tier, ti) {
      const row = byTier[tier] || [];
      const count = row.length || 1;
      const y = TOP_PAD + ti * ROW_GAP;
      row.forEach(function (n, i) {
        const cx = ((i + 1) / (count + 1)) * W;
        pos[n.id] = { x: cx, y: y + NODE_H / 2, node: n };
      });
    });

    const svg = svgEl("svg", {
      width: "100%",
      viewBox: "0 0 " + W + " " + H,
      preserveAspectRatio: "xMidYMid meet",
      style: "display:block",
    });

    // Title (tier legend).
    svg.appendChild(
      svgEl("text", {
        x: 8, y: 18, fill: C.sub, "font-size": "12",
        "font-family": "ui-monospace, monospace",
      })
    ).textContent = txt(data.name, "Topology") + "  ·  tiers: " + tiers.join(" → ");

    // Draw links first (under nodes).
    for (const lk of links) {
      const a = pos[lk && lk.source];
      const b = pos[lk && lk.target];
      if (!a || !b) continue;
      svg.appendChild(
        svgEl("line", {
          x1: a.x, y1: a.y, x2: b.x, y2: b.y,
          stroke: C.border, "stroke-width": "1.5",
        })
      );
    }

    // Draw nodes on top.
    for (const id in pos) {
      drawNode(svg, pos[id], NODE_W, NODE_H);
    }

    box.appendChild(svg);
  }

  /** Render one node: rounded rect tinted by vendor, hostname + bgp + ✓. */
  function drawNode(svg, p, NODE_W, NODE_H) {
    const n = p.node || {};
    const vc = vendorColor(n.vendor);
    const up = n.up === true;
    const x = p.x - NODE_W / 2;
    const y = p.y - NODE_H / 2;

    const g = svgEl("g", null);

    g.appendChild(
      svgEl("rect", {
        x: x, y: y, width: NODE_W, height: NODE_H, rx: 8, ry: 8,
        fill: hexA(vc, 0.15),
        stroke: up ? vc : C.red,
        "stroke-width": up ? "1.5" : "2",
      })
    );

    // Hostname.
    const host = svgEl("text", {
      x: p.x, y: p.y - 4, fill: C.text, "font-size": "12", "font-weight": "600",
      "text-anchor": "middle", "font-family": "ui-monospace, monospace",
    });
    host.textContent = txt(n.id, "?") + (up ? "  ✓" : "");
    g.appendChild(host);

    // BGP summary + driver line.
    const bgpUp = n.bgp_up != null ? n.bgp_up : "?";
    const bgpTot = n.bgp_total != null ? n.bgp_total : "?";
    const sub = svgEl("text", {
      x: p.x, y: p.y + 12, fill: C.sub, "font-size": "10",
      "text-anchor": "middle", "font-family": "ui-monospace, monospace",
    });
    sub.textContent = txt(n.vendor, "?") + " · bgp " + bgpUp + "/" + bgpTot;
    g.appendChild(sub);

    svg.appendChild(g);
  }

  function inferTiers(nodes) {
    const seen = [];
    for (const n of nodes) {
      const t = n && n.tier;
      if (t && seen.indexOf(t) === -1) seen.push(t);
    }
    return seen.length ? seen : ["node"];
  }

  // ---- Orchestration -------------------------------------------------------
  /** Render everything for the active fabric (matrix + relevant topologies). */
  function renderAll() {
    fetchMatrix(currentFabric);
    applyTopologyVisibility(currentFabric);
    if (currentFabric === "all" || currentFabric === "clos") {
      renderTopology("clos", "topoClos");
    }
    if (currentFabric === "all" || currentFabric === "dcn") {
      renderTopology("dcn", "topoDcn");
    }
  }

  function setActiveFabric(fabric) {
    currentFabric = fabric || "all";
    // Toggle the active class on fabric buttons.
    const btns = document.querySelectorAll(".fab-btn");
    btns.forEach(function (b) {
      const isActive = b.getAttribute("data-fabric") === currentFabric;
      b.classList.toggle("active", isActive);
    });
    renderAll();
  }

  function startAuto() {
    stopAuto();
    autoTimer = setInterval(renderAll, REFRESH_MS);
  }

  function stopAuto() {
    if (autoTimer) {
      clearInterval(autoTimer);
      autoTimer = null;
    }
  }

  // ---- Wiring --------------------------------------------------------------
  function init() {
    // Determine initial active fabric from any pre-marked button, else "all".
    const preactive = document.querySelector(".fab-btn.active");
    const initial = preactive && preactive.getAttribute("data-fabric") || "all";

    // Fabric toggle buttons.
    document.querySelectorAll(".fab-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        setActiveFabric(btn.getAttribute("data-fabric") || "all");
      });
    });

    // Manual refresh.
    const refreshBtn = document.getElementById("btnRefresh");
    if (refreshBtn) refreshBtn.addEventListener("click", renderAll);

    // Auto-refresh checkbox.
    const auto = document.getElementById("autoRefresh");
    if (auto) {
      auto.addEventListener("change", function () {
        if (auto.checked) startAuto();
        else stopAuto();
      });
      if (auto.checked) startAuto();
    }

    // Initial paint.
    setActiveFabric(initial);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
