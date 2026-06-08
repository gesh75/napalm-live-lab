#!/usr/bin/env python3
"""Tiny dotted-path resolver with wildcard support — zero dependencies.

Resolves a path like ``data.get_bgp_neighbors.global.peers.*.is_up`` against a
nested dict/list, returning the list of matched values (0, 1, or many). The
``*`` segment expands across all values of a dict or items of a list, so a path
can fan out (e.g. "are ALL peers up?"). Keys that contain dots (BGP peer IPs)
can be quoted: ``...peers."10.0.1.0".is_up``.
"""

from __future__ import annotations


def tokens(path: str) -> list[str]:
    """Split a path on '.' while respecting "double-quoted" segments."""
    out: list[str] = []
    cur = ""
    in_q = False
    for ch in path or "":
        if ch == '"':
            in_q = not in_q
            continue
        if ch == "." and not in_q:
            if cur != "":
                out.append(cur)
                cur = ""
            continue
        cur += ch
    if cur != "":
        out.append(cur)
    return out


def resolve(data, path: str) -> list:
    """Return the list of values matched by ``path`` (flattened across wildcards)."""
    cur = [data]
    for tok in tokens(path):
        nxt = []
        for node in cur:
            if tok == "*":
                if isinstance(node, dict):
                    nxt.extend(node.values())
                elif isinstance(node, list):
                    nxt.extend(node)
            elif isinstance(node, dict):
                if tok in node:
                    nxt.append(node[tok])
            elif isinstance(node, list):
                try:
                    nxt.append(node[int(tok)])
                except (ValueError, IndexError):
                    pass
        cur = nxt
    return cur
