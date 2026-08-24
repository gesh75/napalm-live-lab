#!/usr/bin/env python3
"""Export a SuiteRun as JUnit XML (CI-gradeable), HTML (human), or JSON (raw)."""

from __future__ import annotations

from xml.sax.saxutils import escape, quoteattr


def to_json(run: dict) -> dict:
    return run


def _as_int(value, default: int = 0) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def _empty_run(totals: dict, results: list) -> bool:
    """True when nothing was executed (legacy done/0/0 and fail-closed error)."""
    return not results or _as_int(totals.get("total", 0)) == 0


def _vacuous(run: dict, totals: dict, results: list) -> bool:
    """True when the report must not look green.

    Empty runs are vacuous. ``status=error`` is also a failed report, but it
    is *not* treated as empty when real check results exist — collapsing those
    to tests="1" would drop the actual cases from the JUnit counts.
    """
    return run.get("status") == "error" or _empty_run(totals, results)


def to_junit(run: dict) -> str:
    """JUnit XML — directly consumable by Jenkins / GitHub Actions / GitLab CI."""
    t = run.get("totals") or {}
    results = run.get("results") or []
    empty = _empty_run(t, results)
    # An empty run must emit at least one <error> testcase. Many CI
    # parsers treat tests="0" failures="0" as success even when status=error.
    tests = 1 if empty else t.get("total", 0)
    failures = t.get("failed", 0)
    errors = 1 if empty else t.get("errored", 0)
    lines = ['<?xml version="1.0" encoding="UTF-8"?>']
    lines.append(
        f'<testsuites name={quoteattr(run.get("suite_name",""))} '
        f'tests="{tests}" failures="{failures}" errors="{errors}">')
    lines.append(
        f'  <testsuite name={quoteattr(run.get("suite_id",""))} '
        f'tests="{tests}" failures="{failures}" errors="{errors}" '
        f'timestamp={quoteattr(run.get("started",""))}>')
    if empty:
        err = run.get("error") or "no checks executed"
        lines.append(
            f'    <testcase name="suite_executed" classname={quoteattr(run.get("suite_id",""))}>')
        lines.append(
            f'      <error message={quoteattr(err)}>{escape(str(err))}</error>')
        lines.append('    </testcase>')
    for r in results:
        name = f'{r.get("name","")} [{r.get("hostname","")}]'
        cls = r.get("check_id", "")
        tsec = (r.get("duration_ms", 0) or 0) / 1000.0
        lines.append(f'    <testcase name={quoteattr(name)} classname={quoteattr(cls)} time="{tsec:.3f}">')
        if r.get("errored"):
            lines.append(f'      <error message={quoteattr(r.get("message",""))}>'
                         f'{escape(str(r.get("message","")))}</error>')
        elif not r.get("passed"):
            lines.append(f'      <failure message={quoteattr(r.get("message",""))}>'
                         f'severity={escape(str(r.get("severity","")))}\n'
                         f'{escape(str(r.get("message","")))}</failure>')
        lines.append('    </testcase>')
    lines.append('  </testsuite>')
    lines.append('</testsuites>')
    return "\n".join(lines)


def to_html(run: dict) -> str:
    """Self-contained dark HTML report (GitHub-dark palette)."""
    t = run.get("totals") or {}
    results = run.get("results") or []
    passed = (not _vacuous(run, t, results)
              and t.get("failed", 0) == 0
              and t.get("errored", 0) == 0)
    status_color = "#3fb950" if passed else "#f85149"
    err = run.get("error") or ""
    err_html = (f'<div class="sub" style="color:#f85149">{escape(str(err))}</div>'
                if err else "")
    rows = []
    for r in results:
        if r.get("errored"):
            badge, color = "ERROR", "#d29922"
        elif r.get("passed"):
            badge, color = "PASS", "#3fb950"
        else:
            badge, color = "FAIL", "#f85149"
        rows.append(
            f'<tr><td><span style="color:{color};font-weight:700">{escape(badge)}</span></td>'
            f'<td style="color:#58a6ff;font-family:monospace">{escape(str(r.get("hostname","")))}</td>'
            f'<td>{escape(str(r.get("name","")))}</td>'
            f'<td>{escape(str(r.get("severity","")))}</td>'
            f'<td style="font-family:monospace;color:#8b949e">{escape(str(r.get("message","")))}</td>'
            f'<td style="text-align:right;color:#6e7681">{r.get("duration_ms",0)}ms</td></tr>')
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>Test Run {escape(run.get('run_id',''))}</title>
<style>body{{background:#0d1117;color:#e6edf3;font-family:-apple-system,Segoe UI,sans-serif;margin:0;padding:28px}}
h1{{font-size:20px}} .sub{{color:#8b949e;font-size:13px;margin-bottom:18px}}
.summary{{font-size:22px;font-weight:800;color:{status_color};margin:14px 0}}
table{{width:100%;border-collapse:collapse;font-size:13px;background:#161b22;border:1px solid #30363d;border-radius:8px;overflow:hidden}}
th,td{{padding:9px 12px;border-bottom:1px solid #30363d;text-align:left}}
th{{background:#1c2128;color:#8b949e;text-transform:uppercase;font-size:11px;letter-spacing:.5px}}</style></head>
<body><h1>{escape(run.get('suite_name',''))} <span style="color:#6e7681">· {escape(run.get('run_id',''))}</span></h1>
<div class="sub">fabric: {escape(str(run.get('fabric','')))} · started {escape(run.get('started',''))} · finished {escape(run.get('finished',''))}</div>
<div class="summary">{'PASSED' if status_color=='#3fb950' else 'FAILED'} — {t.get('passed',0)}/{t.get('total',0)} passed
 · {t.get('failed',0)} failed · {t.get('errored',0)} errored</div>
{err_html}
<table><thead><tr><th>Status</th><th>Node</th><th>Check</th><th>Severity</th><th>Detail</th><th>ms</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></body></html>"""
