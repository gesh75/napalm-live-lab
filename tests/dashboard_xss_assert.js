// Standalone node assertion: confirms dashboard.js renderers escape
// device-controlled fields, preventing stored XSS.
//
// We load dashboard.js in a minimal DOM shim, feed a renderer a malicious
// device `model`, and assert the raw payload never reaches innerHTML.

const fs = require("fs");
const path = require("path");
const assert = require("assert");
const vm = require("vm");

const jsPath = path.join(__dirname, "..", "dashboard.js");
const src = fs.readFileSync(jsPath, "utf8");

// Capture the HTML the renderer assigns to resultArea.innerHTML.
let captured = "";
const resultArea = {
    set innerHTML(v) { captured = v; },
    get innerHTML() { return captured; },
};

// Capture for the recent-jobs panel (server-controlled fields, defense-in-depth).
let capturedJobs = "";
const recentJobs = {
    set innerHTML(v) { capturedJobs = v; },
    get innerHTML() { return capturedJobs; },
};

const sandbox = {
    document: {
        getElementById(id) {
            if (id === "resultArea") return resultArea;
            if (id === "recentJobs") return recentJobs;
            return { innerHTML: "", textContent: "", style: {} };
        },
        addEventListener() {},
        querySelectorAll() { return []; },
        querySelector() { return null; },
    },
    window: { addEventListener() {} },
    setInterval() {},
    fetch() { return Promise.resolve({ json: () => Promise.resolve({}) }); },
    console,
};
vm.createContext(sandbox);
vm.runInContext(src, sandbox);

const PAYLOAD = '<img src=x onerror=alert(1)>';

const data = {
    total: 1,
    errors: 0,
    mismatches: [],
    devices: [{
        site: "s1", hostname: "h1", ip: "10.0.0.1", vendor: "v",
        model: PAYLOAD, os_version: PAYLOAD, serial: "abc", uptime: 100, error: null,
    }],
};

sandbox.renderVersionAudit(data);

assert.ok(captured.length > 0, "renderer produced no output");
assert.ok(!captured.includes("<img"), "raw <img payload leaked into innerHTML (XSS)");
assert.ok(captured.includes("&lt;img"), "payload was not HTML-escaped");

console.log("OK: device model escaped, no raw <img in rendered HTML");

// Job panel: feed malicious job fields via a stubbed /api/jobs and assert escape.
sandbox.fetch = () => Promise.resolve({
    json: () => Promise.resolve([
        { status: "done", type: PAYLOAD, site: PAYLOAD, message: PAYLOAD },
    ]),
});

sandbox.updateRecentJobs().then(() => {
    assert.ok(capturedJobs.length > 0, "updateRecentJobs produced no output");
    assert.ok(!capturedJobs.includes("<img"), "raw <img payload leaked into recentJobs (XSS)");
    assert.ok(capturedJobs.includes("&lt;img"), "job fields were not HTML-escaped");
    console.log("OK: job fields (type/site/message) escaped, no raw <img in recentJobs");
}).catch((e) => { console.error(e); process.exit(1); });
