#!/usr/bin/env python3
"""Claude Code Session Viewer — Local web app for browsing session logs."""

import http.server
import json
import html
import os
import re
import urllib.parse
import webbrowser
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path

PORT = int(os.environ.get("CSV_PORT", 18923))
BASE_DIR = os.environ.get(
    "CSV_BASE_DIR",
    os.path.join(os.path.expanduser("~"), ".claude", "projects"),
)
LOCAL_TZ_OFFSET = int(os.environ.get("CSV_TZ_OFFSET", 8))  # default UTC+8
TW = timezone(timedelta(hours=LOCAL_TZ_OFFSET))

# ─── Compact Detection ───
COMPACT_PATTERNS = [
    (r'壓縮進度紀錄|Curator 壓縮於', 'curator', 'Curator 壓縮紀錄'),
    (r'continued from a previous conversation that ran out of context', 'context-cont', 'Context 延續摘要'),
    (r'<command-name>/compact</command-name>', 'compact-cmd', '/compact 指令'),
    (r'Compacted \(ctrl\+o', 'compacted', 'Compacted 輸出'),
    (r'Summary:.*Primary Request|The summary below covers the earlier portion', 'summary', 'Session 摘要'),
]


def detect_compact(text):
    """Return list of (kind, label) for compact content detected in text."""
    if not text:
        return []
    found = []
    for pattern, kind, label in COMPACT_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            found.append((kind, label))
    return found


def fmt_time(ts):
    if not ts:
        return ""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(TW)
        return dt.strftime("%m/%d %H:%M:%S")
    except Exception:
        return str(ts)


def fmt_date(ts):
    if not ts:
        return ""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(TW)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""


def esc(text):
    if not text:
        return ""
    return html.escape(str(text))


def render_md(text):
    if not text:
        return ""
    t = html.escape(text)
    t = re.sub(r'```(\w*)\n(.*?)```', lambda m: f'<pre class="code-block"><code>{m.group(2)}</code></pre>', t, flags=re.DOTALL)
    t = re.sub(r'`([^`]+)`', r'<code class="ic">\1</code>', t)
    t = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', t)
    t = re.sub(r'^(#{1,3}) (.+)$', lambda m: f'<h{len(m.group(1))} style="margin:8px 0">{m.group(2)}</h{len(m.group(1))}>', t, flags=re.MULTILINE)
    t = t.replace("\n", "<br>")
    return t


def compact_badges(compacts):
    """Render compact badges HTML."""
    if not compacts:
        return ""
    badges = ""
    for kind, label in compacts:
        badges += f'<span class="compact-badge compact-{kind}">{esc(label)}</span>'
    return badges


def get_session_summary(filepath):
    """Quick scan: get first user message preview + timestamp + record count + compact info."""
    first_user = ""
    first_ts = ""
    last_ts = ""
    count = 0
    model = ""
    has_compact = False
    compact_count = 0
    try:
        with open(filepath) as f:
            for line in f:
                count += 1
                r = json.loads(line)
                ts = r.get("timestamp", "")
                if ts and not first_ts:
                    first_ts = ts
                if ts:
                    last_ts = ts
                if r.get("type") == "user":
                    msg = r.get("message", {})
                    c = msg.get("content", "")
                    raw = c if isinstance(c, str) else str(c)
                    if not first_user:
                        if isinstance(c, list):
                            c = " ".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
                        first_user = str(c)[:120].replace("\n", " ")
                    if detect_compact(raw):
                        has_compact = True
                        compact_count += 1
                if r.get("type") == "assistant" and not model:
                    model = r.get("message", {}).get("model", "")
    except Exception:
        pass
    return {
        "preview": first_user or "(empty)",
        "start": fmt_date(first_ts),
        "end": fmt_date(last_ts),
        "records": count,
        "model": model,
        "has_compact": has_compact,
        "compact_count": compact_count,
    }


def render_session(filepath):
    """Render a session JSONL into HTML entries."""
    with open(filepath) as f:
        records = [json.loads(line) for line in f]

    parts = []
    eid = 0
    compact_total = 0
    for r in records:
        rtype = r.get("type", "")
        ts = fmt_time(r.get("timestamp", ""))

        if rtype == "user":
            msg = r.get("message", {})
            content = msg.get("content", "")
            if isinstance(content, list):
                text_parts = []
                tool_results = []
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                    elif block.get("type") == "tool_result":
                        tid_full = block.get("tool_use_id", "")
                        tid = tid_full[:12]
                        rc = block.get("content", "")
                        if isinstance(rc, list):
                            rc = "\n".join(b.get("text", "") for b in rc if isinstance(b, dict))
                        rc_str = str(rc)[:500]
                        tool_results.append((tid, tid_full, rc_str))
                if text_parts:
                    text = "\n".join(text_parts)
                    if text.strip():
                        compacts = detect_compact(text)
                        is_compact = " compact" if compacts else ""
                        badges = compact_badges(compacts)
                        if compacts:
                            compact_total += 1
                        parts.append(f'<div class="entry user{is_compact}" data-type="user" data-compact="{1 if compacts else 0}" id="e{eid}">{badges}<span class="tag tag-user">USER</span><span class="time">{ts}</span><button class="cbtn" onclick="fold(this)">▼</button><div class="content">{render_md(text)}</div></div>')
                        eid += 1
                for tid, tid_full, rc_str in tool_results:
                    compacts = detect_compact(rc_str)
                    is_compact = " compact" if compacts else ""
                    badges = compact_badges(compacts)
                    if compacts:
                        compact_total += 1
                    parts.append(f'<div class="entry tool-result{is_compact} api-clickable" data-type="tool" data-compact="{1 if compacts else 0}" data-result-id="{esc(tid_full)}" onclick="jumpToCall(this)" title="Click to jump to tool_use" id="e{eid}">{badges}<span class="tag tag-result">RESULT</span><span class="time">{ts}</span> <span class="tid">id:{tid}…</span><button class="cbtn" onclick="event.stopPropagation();fold(this)">▼</button><div class="content">{esc(rc_str)}</div></div>')
                    eid += 1
            else:
                text_str = str(content)
                if text_str.strip():
                    compacts = detect_compact(text_str)
                    is_compact = " compact" if compacts else ""
                    badges = compact_badges(compacts)
                    if compacts:
                        compact_total += 1
                    parts.append(f'<div class="entry user{is_compact}" data-type="user" data-compact="{1 if compacts else 0}" id="e{eid}">{badges}<span class="tag tag-user">USER</span><span class="time">{ts}</span><button class="cbtn" onclick="fold(this)">▼</button><div class="content">{render_md(text_str)}</div></div>')
                    eid += 1

        elif rtype == "assistant":
            msg = r.get("message", {})
            for block in msg.get("content", []):
                btype = block.get("type", "")
                if btype == "text":
                    text = block.get("text", "")
                    if not text.strip():
                        continue
                    compacts = detect_compact(text)
                    is_compact = " compact" if compacts else ""
                    badges = compact_badges(compacts)
                    if compacts:
                        compact_total += 1
                    parts.append(f'<div class="entry assistant{is_compact}" data-type="assistant" data-compact="{1 if compacts else 0}" id="e{eid}">{badges}<span class="tag tag-assistant">ASSISTANT</span><span class="time">{ts}</span><button class="cbtn" onclick="fold(this)">▼</button><div class="content">{render_md(text)}</div></div>')
                    eid += 1
                elif btype == "thinking":
                    text = block.get("thinking", "")
                    if not text.strip():
                        continue
                    preview = text[:100].replace("\n", " ")
                    parts.append(f'<div class="entry thinking hidden-default" data-type="thinking" data-compact="0" id="e{eid}"><span class="tag tag-thinking">THINKING</span><span class="time">{ts}</span><details><summary>{esc(preview)}…</summary><div class="content">{esc(text)}</div></details></div>')
                    eid += 1
                elif btype == "tool_use":
                    name = block.get("name", "?")
                    tid_full = block.get("id", "")
                    tid = tid_full[:12]
                    inp = block.get("input", {})
                    detail = ""
                    if name == "Bash":
                        detail = inp.get("command", "")
                    elif name in ("Read", "Write"):
                        detail = inp.get("file_path", "")
                    elif name == "Edit":
                        detail = f"file: {inp.get('file_path','')}\nold: {inp.get('old_string','')[:150]}\nnew: {inp.get('new_string','')[:150]}"
                    elif name == "Grep":
                        detail = f"pattern: {inp.get('pattern','')}  path: {inp.get('path','')}"
                    elif name == "Glob":
                        detail = f"pattern: {inp.get('pattern','')}"
                    elif name == "Agent":
                        detail = inp.get("prompt", "")[:300]
                    elif name == "Skill":
                        detail = f"skill: {inp.get('skill','')}"
                    else:
                        detail = json.dumps(inp, ensure_ascii=False)[:400]
                    parts.append(f'<div class="entry tool api-clickable" data-type="tool" data-compact="0" data-call-id="{esc(tid_full)}" onclick="jumpToResult(this)" title="Click to jump to result" id="e{eid}"><span class="tag tag-tool">TOOL</span> <span class="tool-name">{esc(name)}</span> <span class="tid">id:{tid}…</span><span class="time">{ts}</span><button class="cbtn" onclick="event.stopPropagation();fold(this)">▼</button><div class="content">{esc(detail)}</div></div>')
                    eid += 1

        elif rtype == "system":
            sub = r.get("subtype", "")
            if sub:
                parts.append(f'<div class="entry system hidden-default" data-type="system" data-compact="0" id="e{eid}"><span class="tag tag-system">SYS</span><span class="time">{ts}</span> <span style="color:#8b949e">{esc(sub)}</span></div>')
                eid += 1

        elif rtype == "progress":
            data = r.get("data", {})
            dtype = data.get("type", "")
            if dtype == "agent_progress":
                content = data.get("content", "")
                if content and len(str(content)) > 20:
                    preview = str(content)[:100].replace("\n", " ")
                    parts.append(f'<div class="entry progress hidden-default" data-type="progress" data-compact="0" id="e{eid}"><span class="tag tag-progress">AGENT</span><span class="time">{ts}</span><details><summary>{esc(preview)}…</summary><div class="content">{esc(str(content))}</div></details></div>')
                    eid += 1

    return "\n".join(parts), eid, compact_total


def render_api_view(filepath):
    """Render session as API call pairs: request → response."""
    with open(filepath) as f:
        records = [json.loads(line) for line in f]

    from collections import OrderedDict
    api_groups = OrderedDict()
    pending_inputs = []

    for r in records:
        rtype = r.get("type", "")
        if rtype == "user":
            pending_inputs.append(r.get("message", {}))
        elif rtype == "assistant":
            msg = r.get("message", {})
            msg_id = msg.get("id", "")
            if not msg_id:
                continue
            if msg_id not in api_groups:
                api_groups[msg_id] = {
                    "model": msg.get("model", ""),
                    "ts": r.get("timestamp", ""),
                    "turns": [],
                    "stop_reason": msg.get("stop_reason", ""),
                    "usage": msg.get("usage", {}),
                }
            api_groups[msg_id]["turns"].append({
                "inputs": list(pending_inputs),
                "blocks": list(msg.get("content", [])),
            })
            if msg.get("stop_reason"):
                api_groups[msg_id]["stop_reason"] = msg["stop_reason"]
            if msg.get("usage"):
                api_groups[msg_id]["usage"] = msg["usage"]
            pending_inputs = []

    parts = []
    total_in = 0
    total_out = 0
    total_cache_read = 0
    total_cache_create = 0

    for i, (mid, group) in enumerate(api_groups.items()):
        usage = group.get("usage", {})
        in_tok = usage.get("input_tokens", 0)
        out_tok = usage.get("output_tokens", 0)
        cache_read = usage.get("cache_read_input_tokens", 0)
        cache_create = usage.get("cache_creation_input_tokens", 0)
        total_in += in_tok
        total_out += out_tok
        total_cache_read += cache_read
        total_cache_create += cache_create

        stop = group.get("stop_reason", "")
        stop_class = "stop-end" if stop == "end_turn" else "stop-tool" if stop == "tool_use" else "stop-other"
        ts = fmt_time(group.get("ts", ""))

        turn_count = len(group["turns"])

        # Helper to render input messages
        def render_input(m):
            h = ""
            c = m.get("content", "")
            if isinstance(c, str) and c.strip():
                compacts = detect_compact(c)
                cbadges = compact_badges(compacts)
                cclass = " api-compact" if compacts else ""
                h += f'<div class="api-msg api-user{cclass}">{cbadges}<span class="api-role">user</span> <span class="api-size">{len(c):,} chars</span>'
                if len(c) > 300:
                    h += f'<details><summary>{esc(c[:120])}…</summary><div class="api-body">{esc(c)}</div></details>'
                else:
                    h += f'<div class="api-body">{esc(c[:300])}</div>'
                h += '</div>'
            elif isinstance(c, list):
                for block in c:
                    if not isinstance(block, dict):
                        continue
                    bt = block.get("type", "")
                    if bt == "tool_result":
                        tid = block.get("tool_use_id", "")
                        rc = block.get("content", "")
                        if isinstance(rc, list):
                            rc = "\n".join(b.get("text", "") for b in rc if isinstance(b, dict))
                        rc_str = str(rc)
                        is_error = block.get("is_error", False)
                        err_class = " api-error" if is_error else ""
                        compacts = detect_compact(rc_str)
                        cbadges = compact_badges(compacts)
                        cclass = " api-compact" if compacts else ""
                        h += f'<div class="api-msg api-tool-result{err_class}{cclass} api-clickable" data-result-id="{esc(tid)}" onclick="jumpToCall(this)" title="Click to jump to tool_use">{cbadges}<span class="api-role">tool_result</span> <span class="api-tid">id:{tid[:16]}</span> <span class="api-size">{len(rc_str):,} chars</span>'
                        if len(rc_str) > 200:
                            h += f'<details><summary>{esc(rc_str[:120])}…</summary><div class="api-body">{esc(rc_str)}</div></details>'
                        else:
                            h += f'<div class="api-body">{esc(rc_str)}</div>'
                        h += '</div>'
                    elif bt == "text":
                        txt = block.get("text", "")
                        if txt.strip():
                            compacts = detect_compact(txt)
                            cbadges = compact_badges(compacts)
                            cclass = " api-compact" if compacts else ""
                            h += f'<div class="api-msg api-user{cclass}">{cbadges}<span class="api-role">user</span> <span class="api-size">{len(txt):,} chars</span><div class="api-body">{esc(txt[:300])}</div></div>'
            return h

        # Helper to render output blocks
        def render_output(blocks):
            h = ""
            for block in blocks:
                bt = block.get("type", "")
                if bt == "thinking":
                    text = block.get("thinking", "")
                    h += f'<div class="api-msg api-thinking"><span class="api-role">thinking</span> <span class="api-size">{len(text):,} chars</span>'
                    if len(text) > 150:
                        h += f'<details><summary>{esc(text[:100])}…</summary><div class="api-body">{esc(text)}</div></details>'
                    else:
                        h += f'<div class="api-body">{esc(text)}</div>'
                    h += '</div>'
                elif bt == "text":
                    text = block.get("text", "")
                    h += f'<div class="api-msg api-text"><span class="api-role">text</span> <span class="api-size">{len(text):,} chars</span>'
                    if len(text) > 300:
                        h += f'<details><summary>{esc(text[:120])}…</summary><div class="api-body">{esc(text)}</div></details>'
                    else:
                        h += f'<div class="api-body">{esc(text)}</div>'
                    h += '</div>'
                elif bt == "tool_use":
                    name = block.get("name", "?")
                    tid = block.get("id", "")
                    inp = block.get("input", {})
                    inp_json = json.dumps(inp, ensure_ascii=False)
                    h += f'<div class="api-msg api-tool-call api-clickable" data-call-id="{esc(tid)}" onclick="jumpToResult(this)" title="Click to jump to result"><span class="api-role">tool_use</span> <span class="api-tool-name">{esc(name)}</span> <span class="api-tid">id:{tid[:16]}</span> <span class="api-size">{len(inp_json):,} chars</span>'
                    if len(inp_json) > 200:
                        h += f'<details><summary>{esc(inp_json[:120])}…</summary><div class="api-body">{esc(inp_json)}</div></details>'
                    else:
                        h += f'<div class="api-body">{esc(inp_json)}</div>'
                    h += '</div>'
            return h

        # Build turns HTML — show each turn as a paired row
        turns_html = ""
        for j, turn in enumerate(group["turns"]):
            req_html = ""
            for m in turn["inputs"]:
                req_html += render_input(m)
            resp_html = render_output(turn["blocks"])

            if turn_count == 1:
                # Single turn: simple layout
                turns_html += f"""<div class="api-pair">
    <div class="api-req"><div class="api-label">REQUEST</div>{req_html or '<div class="api-empty">(streaming continuation)</div>'}</div>
    <div class="api-arrow">→</div>
    <div class="api-resp"><div class="api-label">RESPONSE</div>{resp_html}</div>
</div>"""
            else:
                # Multi-turn: show turn number and connect them visually
                turn_label = f'<div class="turn-label">Turn {j+1}/{turn_count}</div>'
                connector = ' turn-first' if j == 0 else ' turn-mid' if j < turn_count - 1 else ' turn-last'
                turns_html += f"""<div class="api-pair api-turn{connector}">
    <div class="api-req">{turn_label}{req_html or '<div class="api-empty">(streaming)</div>'}</div>
    <div class="api-arrow">{'→' if req_html else '↓'}</div>
    <div class="api-resp">{resp_html}</div>
</div>"""

        # Token bar visualization
        tok_bar = ""
        if in_tok or out_tok or cache_read or cache_create:
            total = max(in_tok + cache_read + cache_create, 1)
            tok_bar = f"""<div class="tok-bar">
                <div class="tok-seg tok-cache-read" style="width:{cache_read/total*100:.1f}%" title="cache read: {cache_read:,}"></div>
                <div class="tok-seg tok-cache-create" style="width:{cache_create/total*100:.1f}%" title="cache create: {cache_create:,}"></div>
                <div class="tok-seg tok-input" style="width:{in_tok/total*100:.1f}%" title="input: {in_tok:,}"></div>
            </div>"""

        parts.append(f"""<div class="api-call" id="api{i}">
  <div class="api-header">
    <span class="api-num">#{i+1}</span>
    <span class="api-model">{esc(group['model'])}</span>
    <span class="api-time">{ts}</span>
    <span class="api-stop {stop_class}">{esc(stop)}</span>
    <span class="api-turns">{turn_count} turn{'s' if turn_count > 1 else ''}</span>
    <span class="api-tokens">in:{in_tok:,} out:{out_tok:,} cache_r:{cache_read:,} cache_w:{cache_create:,}</span>
    <span class="api-mid">{esc(mid[:24])}</span>
  </div>
  {tok_bar}
  {turns_html}
</div>""")

    # Summary header
    summary = f"""<div class="api-summary">
  <strong>{len(api_groups)} API Calls</strong> |
  Total tokens — input: {total_in:,} | output: {total_out:,} | cache read: {total_cache_read:,} | cache create: {total_cache_create:,} |
  Est. cost: ~${(total_in * 15 + total_out * 75 + total_cache_read * 1.5 + total_cache_create * 18.75) / 1_000_000:.2f}
</div>"""

    return summary + "\n".join(parts), len(api_groups)


# ─── CSS ───
CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, "Segoe UI", sans-serif; background: #0d1117; color: #c9d1d9; }
a { color: #58a6ff; text-decoration: none; }
a:hover { text-decoration: underline; }

/* Layout */
.sidebar { position: fixed; left: 0; top: 0; bottom: 0; width: 300px; background: #010409; border-right: 1px solid #21262d; overflow-y: auto; padding: 12px; z-index: 10; }
.main { margin-left: 300px; padding: 20px; max-width: 900px; }

/* Sidebar */
.sidebar h2 { color: #58a6ff; font-size: 1.1em; margin-bottom: 8px; display: flex; align-items: center; gap: 6px; }
.sidebar input { width: 100%; background: #0d1117; color: #c9d1d9; border: 1px solid #30363d; padding: 6px 8px; border-radius: 6px; font-size: 0.85em; margin-bottom: 8px; }
.folder { margin-bottom: 2px; }
.folder-name { display: block; padding: 5px 8px; border-radius: 4px; cursor: pointer; font-size: 0.82em; color: #8b949e; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.folder-name:hover { background: #161b22; color: #c9d1d9; }
.folder-name.active { background: #1f6feb22; color: #58a6ff; }
.sessions { display: none; padding-left: 12px; }
.sessions.open { display: block; }
.session-item { display: block; padding: 6px 8px; border-radius: 4px; cursor: pointer; font-size: 0.78em; margin-bottom: 2px; border-left: 2px solid transparent; }
.session-item:hover { background: #161b22; }
.session-item.active { background: #1f6feb15; border-left-color: #58a6ff; }
.session-preview { color: #8b949e; font-size: 0.9em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 240px; }
.session-meta { color: #484f58; font-size: 0.85em; margin-top: 2px; }
.compact-indicator { display: inline-block; background: #a371f722; color: #a371f7; font-size: 0.8em; padding: 0 4px; border-radius: 3px; margin-left: 4px; }

/* Main content */
.header { margin-bottom: 12px; }
.header h1 { color: #58a6ff; font-size: 1.3em; }
.header .meta { color: #8b949e; font-size: 0.82em; margin-top: 2px; }
.compact-summary { background: #a371f712; border: 1px solid #a371f733; border-radius: 8px; padding: 10px 14px; margin-bottom: 14px; font-size: 0.85em; color: #a371f7; }
.compact-summary strong { color: #d2a8ff; }
.filters { margin-bottom: 14px; display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
.filters button { background: #21262d; color: #c9d1d9; border: 1px solid #30363d; padding: 3px 10px; border-radius: 6px; cursor: pointer; font-size: 0.8em; transition: all 0.15s; }
.filters button:hover { border-color: #484f58; }
.filters button.active { background: #388bfd26; border-color: #388bfd; color: #58a6ff; }
.filters button.compact-filter.active { background: #a371f722; border-color: #a371f7; color: #a371f7; }
#search { background: #0d1117; color: #c9d1d9; border: 1px solid #30363d; padding: 5px 8px; border-radius: 6px; width: 220px; font-size: 0.85em; }

/* Entries */
.entry { margin-bottom: 8px; border-left: 3px solid #30363d; padding: 6px 10px; border-radius: 0 6px 6px 0; background: #161b22; transition: all 0.15s; position: relative; }
.entry.user { border-left-color: #3fb950; }
.entry.assistant { border-left-color: #58a6ff; }
.entry.tool { border-left-color: #d29922; }
.entry.tool-result { border-left-color: #d2992280; }
.entry.thinking { border-left-color: #484f58; }
.entry.system { border-left-color: #f85149; }
.entry.progress { border-left-color: #6e7681; }

/* Compact entries */
.entry.compact { border-left-color: #a371f7 !important; background: #1a1028; box-shadow: inset 0 0 0 1px #a371f722; }
.entry.compact::before { content: ''; position: absolute; top: 0; right: 0; width: 0; height: 0; border-top: 16px solid #a371f7; border-left: 16px solid transparent; border-radius: 0 6px 0 0; }
.compact-badge { display: inline-block; font-size: 0.65em; padding: 1px 6px; border-radius: 3px; margin-right: 4px; font-weight: 600; background: #a371f730; color: #d2a8ff; border: 1px solid #a371f744; letter-spacing: 0.3px; }
.compact-curator { background: #a371f730; color: #d2a8ff; border-color: #a371f744; }
.compact-context-cont { background: #da363330; color: #ff7b72; border-color: #da363344; }
.compact-compact-cmd { background: #3fb95030; color: #7ee787; border-color: #3fb95044; }
.compact-compacted { background: #d2992230; color: #e3b341; border-color: #d2992244; }
.compact-summary { background: #1f6feb30; color: #79c0ff; border-color: #1f6feb44; }

/* Only-compact mode */
.only-compact .entry:not(.compact) { display: none !important; }

.tag { display: inline-block; font-size: 0.7em; padding: 1px 5px; border-radius: 3px; margin-right: 4px; font-weight: 600; }
.tag-user { background: #238636; color: #fff; }
.tag-assistant { background: #1f6feb; color: #fff; }
.tag-tool { background: #9e6a03; color: #fff; }
.tag-result { background: #9e6a0366; color: #d29922; }
.tag-thinking { background: #30363d; color: #8b949e; }
.tag-system { background: #da3633; color: #fff; }
.tag-progress { background: #21262d; color: #6e7681; }
.time { color: #484f58; font-size: 0.75em; margin-left: 4px; }
.tid { color: #6e7681; font-size: 0.7em; font-family: monospace; }
.tool-name { color: #d29922; font-weight: 600; font-size: 0.9em; }
.cbtn { background: none; border: none; color: #484f58; cursor: pointer; font-size: 0.7em; margin-left: 6px; }
.content { margin-top: 5px; font-size: 0.88em; line-height: 1.55; white-space: pre-wrap; word-break: break-word; }
.folded .content { display: none; }
.folded .cbtn { color: #58a6ff; }
pre.code-block { background: #0d1117; border: 1px solid #30363d; border-radius: 4px; padding: 6px; overflow-x: auto; margin: 4px 0; font-size: 0.9em; }
code.ic { background: #30363d; padding: 1px 3px; border-radius: 3px; font-size: 0.9em; }
.hidden-default { display: none; }
.show-hidden .hidden-default { display: block; }
.entry.filtered-out { display: none; }

/* API View */
.api-summary { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 10px 14px; margin-bottom: 16px; font-size: 0.85em; color: #8b949e; }
.api-summary strong { color: #58a6ff; }
.api-call { background: #161b22; border: 1px solid #21262d; border-radius: 8px; margin-bottom: 12px; overflow: hidden; }
.api-header { display: flex; align-items: center; gap: 8px; padding: 8px 12px; background: #0d1117; border-bottom: 1px solid #21262d; font-size: 0.78em; flex-wrap: wrap; }
.api-num { color: #58a6ff; font-weight: 700; font-size: 1.1em; }
.api-model { color: #8b949e; }
.api-time { color: #484f58; }
.api-stop { padding: 1px 6px; border-radius: 3px; font-size: 0.9em; font-weight: 600; }
.stop-end { background: #23863622; color: #3fb950; }
.stop-tool { background: #9e6a0322; color: #d29922; }
.stop-other { background: #30363d; color: #8b949e; }
.api-turns { color: #6e7681; }
.api-tokens { color: #484f58; font-family: monospace; font-size: 0.9em; }
.api-mid { color: #30363d; font-family: monospace; font-size: 0.85em; margin-left: auto; }
.tok-bar { display: flex; height: 3px; background: #21262d; }
.tok-seg { height: 100%; }
.tok-cache-read { background: #3fb95088; }
.tok-cache-create { background: #d2992288; }
.tok-input { background: #58a6ff88; }
.api-pair { display: grid; grid-template-columns: 1fr auto 1fr; gap: 0; min-height: 40px; }
.api-req { padding: 8px 10px; border-right: 1px solid #21262d; }
.api-resp { padding: 8px 10px; }
.api-arrow { display: flex; align-items: center; justify-content: center; color: #30363d; font-size: 1.3em; padding: 0 6px; background: #0d1117; }
.api-label { font-size: 0.65em; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; color: #484f58; margin-bottom: 6px; }
.api-msg { margin-bottom: 6px; padding: 4px 6px; border-radius: 4px; font-size: 0.82em; border-left: 2px solid #30363d; }
.api-msg.api-user { border-left-color: #3fb950; background: #23863608; }
.api-msg.api-tool-result { border-left-color: #d2992280; background: #9e6a0308; }
.api-msg.api-error { border-left-color: #f85149; background: #da363308; }
.api-msg.api-thinking { border-left-color: #484f58; background: #30363d10; }
.api-msg.api-text { border-left-color: #58a6ff; background: #1f6feb08; }
.api-msg.api-tool-call { border-left-color: #d29922; background: #9e6a0310; }
.api-msg.api-compact { box-shadow: inset 0 0 0 1px #a371f722; }
.api-role { font-size: 0.85em; font-weight: 600; color: #6e7681; }
.api-size { font-size: 0.8em; color: #484f58; font-family: monospace; }
.api-tid { font-size: 0.75em; color: #484f58; font-family: monospace; }
.api-tool-name { color: #d29922; font-weight: 600; }
.api-body { margin-top: 3px; color: #8b949e; font-size: 0.92em; white-space: pre-wrap; word-break: break-word; max-height: 200px; overflow-y: auto; }
.api-empty { color: #30363d; font-style: italic; font-size: 0.85em; }
/* Multi-turn styling */
.turn-label { font-size: 0.65em; font-weight: 700; color: #484f58; background: #21262d; display: inline-block; padding: 1px 6px; border-radius: 3px; margin-bottom: 4px; }
.api-turn { border-top: 1px dashed #21262d; }
.api-turn.turn-first { border-top: none; }
.api-turn .api-arrow { color: #21262d; font-size: 1em; }
.api-turn.turn-first .api-req::before,
.api-turn.turn-mid .api-req::before,
.api-turn.turn-last .api-req::before { content: ''; position: absolute; left: -1px; top: 0; bottom: 0; width: 2px; background: linear-gradient(180deg, #58a6ff33, #58a6ff11); }
.api-turn .api-req { position: relative; }
.view-toggle { display: flex; gap: 0; margin-bottom: 14px; }
.view-toggle button { background: #21262d; color: #8b949e; border: 1px solid #30363d; padding: 5px 14px; font-size: 0.82em; cursor: pointer; transition: all 0.15s; }
.view-toggle button:first-child { border-radius: 6px 0 0 6px; }
.view-toggle button:last-child { border-radius: 0 6px 6px 0; }
.view-toggle button.active { background: #1f6feb22; color: #58a6ff; border-color: #1f6feb; }

/* Floating nav toolbar */
.nav-toolbar { position: fixed; right: 20px; bottom: 20px; display: flex; flex-direction: column; gap: 6px; z-index: 100; opacity: 0; pointer-events: none; transition: opacity 0.2s; }
.nav-toolbar.visible { opacity: 1; pointer-events: auto; }
.nav-btn { width: 40px; height: 40px; border-radius: 10px; background: #21262d; border: 1px solid #30363d; color: #8b949e; cursor: pointer; display: flex; align-items: center; justify-content: center; font-size: 16px; transition: all 0.15s; position: relative; }
.nav-btn:hover { background: #30363d; color: #c9d1d9; border-color: #484f58; }
.nav-btn:hover .nav-tip { opacity: 1; transform: translateX(0); pointer-events: auto; }
.nav-tip { position: absolute; right: 50px; background: #161b22; border: 1px solid #30363d; color: #8b949e; padding: 3px 8px; border-radius: 4px; font-size: 0.72em; white-space: nowrap; opacity: 0; transform: translateX(6px); transition: all 0.15s; pointer-events: none; }

/* Scroll progress bar */
.scroll-progress { position: fixed; top: 0; left: 300px; right: 0; height: 3px; background: #21262d; z-index: 50; }
.scroll-progress-bar { height: 100%; background: linear-gradient(90deg, #58a6ff, #a371f7); width: 0%; transition: width 0.1s; border-radius: 0 2px 2px 0; }

/* Entry position indicator */
.position-indicator { position: fixed; right: 20px; top: 20px; background: #161b22; border: 1px solid #30363d; border-radius: 6px; padding: 4px 10px; font-size: 0.75em; color: #484f58; z-index: 50; opacity: 0; transition: opacity 0.3s; pointer-events: none; }
.position-indicator.visible { opacity: 1; }

/* Minimap */
.minimap { position: fixed; right: 4px; top: 60px; bottom: 80px; width: 10px; background: #0d1117; border-radius: 4px; z-index: 50; opacity: 0; transition: opacity 0.2s; cursor: pointer; }
.minimap.visible { opacity: 1; }
.minimap:hover { width: 14px; }
.minimap-dot { position: absolute; left: 1px; right: 1px; height: 2px; border-radius: 1px; }
.minimap-dot.mm-user { background: #3fb950; }
.minimap-dot.mm-assistant { background: #58a6ff; }
.minimap-dot.mm-tool { background: #d29922; }
.minimap-dot.mm-compact { background: #a371f7; }
.minimap-viewport { position: absolute; left: -1px; right: -1px; background: #c9d1d915; border: 1px solid #58a6ff44; border-radius: 2px; pointer-events: none; }

/* Sticky filters bar */
.filters { margin-bottom: 14px; display: flex; gap: 6px; flex-wrap: wrap; align-items: center; position: sticky; top: 0; background: #0d1117; z-index: 20; padding: 8px 0; border-bottom: 1px solid #21262d; }

/* Keyboard shortcut hint */
.kbd { display: inline-block; background: #21262d; border: 1px solid #30363d; border-radius: 3px; padding: 0 4px; font-size: 0.7em; font-family: monospace; color: #6e7681; margin-left: 4px; }

/* Welcome */
.welcome { text-align: center; margin-top: 100px; color: #484f58; }
.welcome h2 { color: #58a6ff; font-size: 1.6em; margin-bottom: 8px; }
.welcome p { font-size: 0.95em; }
.welcome .logo { font-size: 3em; margin-bottom: 12px; opacity: 0.6; }

/* Loading */
.loading { text-align: center; color: #8b949e; margin-top: 60px; }

/* Jump-to highlight — double blink */
.entry.highlight-jump, .api-msg.highlight-jump { animation: doubleBlink 1s ease-out; }
@keyframes doubleBlink {
  0%   { box-shadow: 0 0 0 3px #58a6ff88; background-color: #58a6ff15; }
  25%  { box-shadow: none; background-color: transparent; }
  40%  { box-shadow: 0 0 0 3px #58a6ff88; background-color: #58a6ff15; }
  70%  { box-shadow: none; background-color: transparent; }
  100% { box-shadow: none; background-color: transparent; }
}

/* Clickable tool links */
.api-clickable { cursor: pointer; transition: all 0.15s; }
.api-clickable:hover { filter: brightness(1.2); box-shadow: 0 0 0 1px #58a6ff44; }
.api-clickable .api-tid, .api-clickable .tid { text-decoration: underline; text-decoration-style: dashed; text-underline-offset: 2px; }

/* Scrollbar */
::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: #30363d; border-radius: 3px; }
"""

# ─── Handler ───
class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        params = urllib.parse.parse_qs(parsed.query)

        if path == "/":
            self.send_html(self.page_index())
        elif path == "/api/folders":
            self.send_json(self.api_folders())
        elif path == "/api/sessions":
            folder = params.get("folder", [""])[0]
            self.send_json(self.api_sessions(folder))
        elif path == "/api/view":
            file = params.get("file", [""])[0]
            self.send_html_fragment(self.api_view(file))
        elif path == "/api/apiview":
            file = params.get("file", [""])[0]
            self.send_html_fragment(self.api_apiview(file))
        else:
            self.send_error(404)

    def send_html(self, content):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(content.encode())

    def send_html_fragment(self, content):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(content.encode())

    def send_json(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def api_folders(self):
        folders = []
        try:
            for name in sorted(os.listdir(BASE_DIR)):
                full = os.path.join(BASE_DIR, name)
                if os.path.isdir(full):
                    cnt = sum(1 for f in os.listdir(full) if f.endswith(".jsonl"))
                    if cnt > 0:
                        readable = name.replace("-", "/")
                        folders.append({"name": name, "readable": readable, "count": cnt})
        except Exception as e:
            return {"error": str(e)}
        return folders

    def api_sessions(self, folder):
        sessions = []
        folder_path = os.path.join(BASE_DIR, folder)
        if not os.path.isdir(folder_path):
            return {"error": "not found"}
        jsonl_files = sorted(
            [f for f in os.listdir(folder_path) if f.endswith(".jsonl")],
            key=lambda f: os.path.getmtime(os.path.join(folder_path, f)),
            reverse=True,
        )
        for fname in jsonl_files:
            fpath = os.path.join(folder_path, fname)
            summary = get_session_summary(fpath)
            sessions.append({
                "filename": fname,
                "session_id": fname.replace(".jsonl", ""),
                "path": fpath,
                **summary,
            })
        return sessions

    def api_view(self, filepath):
        if not filepath or not os.path.isfile(filepath):
            return '<div class="loading">File not found</div>'
        try:
            content, count, compact_total = render_session(filepath)
            sid = Path(filepath).stem
            compact_bar = ""
            if compact_total > 0:
                compact_bar = f'<div class="compact-summary"><strong>Compact Content Detected:</strong> {compact_total} entries contain compressed/summarized content (purple border + corner marker)</div>'
            return f'<div class="header"><h1>Session: {esc(sid[:20])}…</h1><div class="meta">{count} entries | {compact_total} compact | {esc(filepath)}</div></div>{compact_bar}{content}'
        except Exception as e:
            return f'<div class="loading">Error: {esc(str(e))}</div>'

    def api_apiview(self, filepath):
        if not filepath or not os.path.isfile(filepath):
            return '<div class="loading">File not found</div>'
        try:
            content, count = render_api_view(filepath)
            sid = Path(filepath).stem
            return f'<div class="header"><h1>API View: {esc(sid[:20])}…</h1><div class="meta">{count} API calls | {esc(filepath)}</div></div>{content}'
        except Exception as e:
            import traceback
            return f'<div class="loading">Error: {esc(str(e))}<br><pre>{esc(traceback.format_exc())}</pre></div>'

    def page_index(self):
        return f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<title>Claude Session Viewer</title>
<style>{CSS}</style>
</head>
<body>

<div class="sidebar">
  <h2><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#58a6ff" stroke-width="2"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg> Sessions</h2>
  <input type="text" id="folder-search" placeholder="搜尋專案資料夾..." oninput="filterFolders(this.value)">
  <div id="folder-list"></div>
</div>

<!-- Scroll progress -->
<div class="scroll-progress"><div class="scroll-progress-bar" id="scroll-bar"></div></div>

<!-- Position indicator -->
<div class="position-indicator" id="pos-indicator"></div>

<!-- Minimap -->
<div class="minimap" id="minimap"><div class="minimap-viewport" id="minimap-vp"></div></div>

<!-- Floating nav toolbar -->
<div class="nav-toolbar" id="nav-toolbar">
  <button class="nav-btn" onclick="goTop()" title="回到頂端"><span class="nav-tip">頂端 <span class="kbd">T</span></span>&#x25B2;</button>
  <button class="nav-btn" onclick="goBottom()" title="跳到底部"><span class="nav-tip">底部 <span class="kbd">B</span></span>&#x25BC;</button>
  <button class="nav-btn" onclick="jumpPrev()" title="上一個 User"><span class="nav-tip">上一個 User <span class="kbd">K</span></span>&#x25C0;</button>
  <button class="nav-btn" onclick="jumpNext()" title="下一個 User"><span class="nav-tip">下一個 User <span class="kbd">J</span></span>&#x25B6;</button>
  <button class="nav-btn" onclick="jumpPrevCompact()" title="上一個 Compact"><span class="nav-tip">上一個 Compact <span class="kbd">P</span></span><span style="color:#a371f7">&#x25C0;</span></button>
  <button class="nav-btn" onclick="jumpNextCompact()" title="下一個 Compact"><span class="nav-tip">下一個 Compact <span class="kbd">N</span></span><span style="color:#a371f7">&#x25B6;</span></button>
</div>

<div class="main">
  <div id="viewer">
    <div class="welcome">
      <div class="logo">
        <svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="#30363d" stroke-width="1.5"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg>
      </div>
      <h2>Claude Code Session Viewer</h2>
      <p>從左側選擇專案和 session 開始瀏覽</p>
      <div style="margin-top:20px;color:#30363d;font-size:0.8em">
        快捷鍵：<span class="kbd">T</span> 頂端 <span class="kbd">B</span> 底部 <span class="kbd">J</span>/<span class="kbd">K</span> 下/上一個 User <span class="kbd">N</span>/<span class="kbd">P</span> 下/上一個 Compact
      </div>
    </div>
  </div>
</div>

<script>
let currentFolder = null;
let currentFile = null;

async function loadFolders() {{
  const res = await fetch('/api/folders');
  const folders = await res.json();
  const list = document.getElementById('folder-list');
  list.innerHTML = '';
  folders.forEach(f => {{
    const div = document.createElement('div');
    div.className = 'folder';
    div.dataset.name = f.name;
    div.dataset.readable = f.readable;
    div.innerHTML = `
      <div class="folder-name" onclick="toggleFolder(this, '${{f.name}}')" title="${{f.readable}}">
        ${{f.readable.split('/').pop() || f.readable}} <span style="color:#484f58;font-size:0.85em">(${{f.count}})</span>
      </div>
      <div class="sessions" id="sessions-${{f.name}}"></div>
    `;
    list.appendChild(div);
  }});
}}

async function toggleFolder(el, folder) {{
  const sessDiv = document.getElementById('sessions-' + folder);
  if (sessDiv.classList.contains('open')) {{
    sessDiv.classList.remove('open');
    el.classList.remove('active');
    return;
  }}
  document.querySelectorAll('.sessions.open').forEach(s => s.classList.remove('open'));
  document.querySelectorAll('.folder-name.active').forEach(e => e.classList.remove('active'));
  el.classList.add('active');
  sessDiv.classList.add('open');
  sessDiv.innerHTML = '<div style="color:#484f58;font-size:0.8em;padding:4px">Loading...</div>';

  const res = await fetch('/api/sessions?folder=' + encodeURIComponent(folder));
  const sessions = await res.json();
  sessDiv.innerHTML = '';
  sessions.forEach(s => {{
    const item = document.createElement('div');
    item.className = 'session-item';
    item.dataset.path = s.path;
    item.onclick = () => loadSession(s.path, item);
    const compactTag = s.has_compact ? `<span class="compact-indicator">C:${{s.compact_count}}</span>` : '';
    item.innerHTML = `
      <div class="session-preview">${{escHtml(s.preview)}}${{compactTag}}</div>
      <div class="session-meta">${{s.start}} · ${{s.records}} records · ${{s.model || '?'}}</div>
    `;
    sessDiv.appendChild(item);
  }});
}}

async function loadSession(filepath, el) {{
  document.querySelectorAll('.session-item.active').forEach(e => e.classList.remove('active'));
  if (el) el.classList.add('active');
  currentFile = filepath;

  const viewer = document.getElementById('viewer');
  viewer.innerHTML = '<div class="loading">載入中...</div>';

  const res = await fetch('/api/view?file=' + encodeURIComponent(filepath));
  const html = await res.text();

  viewer.innerHTML = `
    <div class="view-toggle">
      <button class="active" onclick="switchView('chat', this)">Chat View</button>
      <button onclick="switchView('api', this)">API View</button>
    </div>
    <div id="chat-view">
      <div class="filters">
        <button class="active" data-f="user" onclick="tf(this)">User</button>
        <button class="active" data-f="assistant" onclick="tf(this)">Assistant</button>
        <button class="active" data-f="tool" onclick="tf(this)">Tool</button>
        <button data-f="thinking" onclick="tf(this)">Thinking</button>
        <button data-f="system" onclick="tf(this)">System</button>
        <button data-f="progress" onclick="tf(this)">Progress</button>
        <button class="compact-filter" data-f="compact" onclick="toggleCompactOnly(this)">Only Compact</button>
        <input type="text" id="search" placeholder="搜尋..." oninput="doSearch(this.value)">
        <button onclick="foldAll()" style="margin-left:auto">全部摺疊</button>
        <button onclick="unfoldAll()">全部展開</button>
      </div>
      <div id="entries">${{html}}</div>
    </div>
    <div id="api-view" style="display:none"></div>
  `;
  applyFilters();
}}

const activeFilters = new Set(['user', 'assistant', 'tool']);
let searchVal = '';
let compactOnly = false;

function tf(btn) {{
  const f = btn.dataset.f;
  if (activeFilters.has(f)) {{
    activeFilters.delete(f);
    btn.classList.remove('active');
  }} else {{
    activeFilters.add(f);
    btn.classList.add('active');
  }}
  const showHidden = activeFilters.has('thinking') || activeFilters.has('system') || activeFilters.has('progress');
  document.getElementById('entries').classList.toggle('show-hidden', showHidden);
  applyFilters();
}}

function toggleCompactOnly(btn) {{
  compactOnly = !compactOnly;
  btn.classList.toggle('active', compactOnly);
  document.getElementById('entries').classList.toggle('only-compact', compactOnly);
}}

function doSearch(val) {{
  searchVal = val.toLowerCase();
  applyFilters();
}}

function applyFilters() {{
  document.querySelectorAll('#entries > .entry').forEach(el => {{
    const t = el.dataset.type;
    const matchF = activeFilters.has(t);
    const matchS = !searchVal || el.textContent.toLowerCase().includes(searchVal);
    el.classList.toggle('filtered-out', !(matchF && matchS));
  }});
}}

function fold(btn) {{
  btn.closest('.entry').classList.toggle('folded');
}}
function foldAll() {{
  document.querySelectorAll('#entries > .entry').forEach(e => e.classList.add('folded'));
}}
function unfoldAll() {{
  document.querySelectorAll('#entries > .entry').forEach(e => e.classList.remove('folded'));
}}

function filterFolders(val) {{
  val = val.toLowerCase();
  document.querySelectorAll('.folder').forEach(f => {{
    const match = f.dataset.readable.toLowerCase().includes(val) || f.dataset.name.toLowerCase().includes(val);
    f.style.display = match ? '' : 'none';
  }});
}}

function escHtml(s) {{
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}}

// ─── Tool ID Jump ───
function getActiveView() {{
  // Return the currently visible view container
  const apiDiv = document.getElementById('api-view');
  if (apiDiv && apiDiv.style.display !== 'none' && apiDiv.innerHTML.trim()) {{
    return apiDiv;
  }}
  return document.getElementById('chat-view') || document;
}}

function blinkTarget(target) {{
  // Unfold if folded
  if (target.classList.contains('folded')) target.classList.remove('folded');
  // If inside a closed <details>, open it
  const details = target.closest('details');
  if (details && !details.open) details.open = true;
  // Scroll and double-blink
  target.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
  target.classList.remove('highlight-jump');
  void target.offsetWidth;
  target.classList.add('highlight-jump');
  setTimeout(() => target.classList.remove('highlight-jump'), 1100);
}}

function jumpToResult(el) {{
  const callId = el.dataset.callId;
  if (!callId) return;
  // Search within the currently active view first
  const view = getActiveView();
  let target = view.querySelector(`[data-result-id="${{callId}}"]`);
  // Fallback: search entire document
  if (!target) target = document.querySelector(`[data-result-id="${{callId}}"]`);
  if (target) {{
    blinkTarget(target);
  }} else {{
    // Not found — flash red dashed outline on the source
    el.style.outline = '2px dashed #f85149';
    el.style.outlineOffset = '2px';
    setTimeout(() => {{ el.style.outline = ''; el.style.outlineOffset = ''; }}, 1000);
  }}
}}

function jumpToCall(el) {{
  const resultId = el.dataset.resultId;
  if (!resultId) return;
  const view = getActiveView();
  let target = view.querySelector(`[data-call-id="${{resultId}}"]`);
  if (!target) target = document.querySelector(`[data-call-id="${{resultId}}"]`);
  if (target) {{
    blinkTarget(target);
  }} else {{
    el.style.outline = '2px dashed #f85149';
    el.style.outlineOffset = '2px';
    setTimeout(() => {{ el.style.outline = ''; el.style.outlineOffset = ''; }}, 1000);
  }}
}}

// ─── View Toggle ───
let apiViewLoaded = false;

async function switchView(view, btn) {{
  document.querySelectorAll('.view-toggle button').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');

  const chatDiv = document.getElementById('chat-view');
  const apiDiv = document.getElementById('api-view');

  if (view === 'chat') {{
    chatDiv.style.display = '';
    apiDiv.style.display = 'none';
  }} else {{
    chatDiv.style.display = 'none';
    apiDiv.style.display = '';
    if (!apiViewLoaded && currentFile) {{
      apiDiv.innerHTML = '<div class="loading">載入 API View...</div>';
      const res = await fetch('/api/apiview?file=' + encodeURIComponent(currentFile));
      apiDiv.innerHTML = await res.text();
      apiViewLoaded = true;
    }}
  }}
}}

// ─── Navigation ───
let sessionLoaded = false;

function goTop() {{ window.scrollTo({{top: 0, behavior: 'smooth'}}); }}
function goBottom() {{ window.scrollTo({{top: document.body.scrollHeight, behavior: 'smooth'}}); }}

function getVisibleEntries(selector) {{
  return [...document.querySelectorAll(selector)].filter(el => {{
    return !el.classList.contains('filtered-out') && !el.classList.contains('hidden-default') && el.offsetParent !== null;
  }});
}}

function jumpToEntry(el) {{
  if (!el) return;
  el.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
  el.classList.add('highlight-jump');
  setTimeout(() => el.classList.remove('highlight-jump'), 1200);
}}

function getCurrentEntryIndex(entries) {{
  const scrollY = window.scrollY + window.innerHeight / 3;
  for (let i = entries.length - 1; i >= 0; i--) {{
    if (entries[i].offsetTop <= scrollY) return i;
  }}
  return -1;
}}

function jumpNext() {{
  const entries = getVisibleEntries('#entries > .entry[data-type="user"]');
  const idx = getCurrentEntryIndex(entries);
  if (idx < entries.length - 1) jumpToEntry(entries[idx + 1]);
}}
function jumpPrev() {{
  const entries = getVisibleEntries('#entries > .entry[data-type="user"]');
  const idx = getCurrentEntryIndex(entries);
  if (idx > 0) jumpToEntry(entries[idx - 1]);
  else if (entries.length) jumpToEntry(entries[0]);
}}
function jumpNextCompact() {{
  const entries = getVisibleEntries('#entries > .entry.compact');
  const idx = getCurrentEntryIndex(entries);
  if (idx < entries.length - 1) jumpToEntry(entries[idx + 1]);
  else if (entries.length && idx === -1) jumpToEntry(entries[0]);
}}
function jumpPrevCompact() {{
  const entries = getVisibleEntries('#entries > .entry.compact');
  const idx = getCurrentEntryIndex(entries);
  if (idx > 0) jumpToEntry(entries[idx - 1]);
  else if (entries.length) jumpToEntry(entries[0]);
}}

// Keyboard shortcuts
document.addEventListener('keydown', e => {{
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
  if (!sessionLoaded) return;
  switch(e.key.toLowerCase()) {{
    case 't': goTop(); e.preventDefault(); break;
    case 'b': goBottom(); e.preventDefault(); break;
    case 'j': jumpNext(); e.preventDefault(); break;
    case 'k': jumpPrev(); e.preventDefault(); break;
    case 'n': jumpNextCompact(); e.preventDefault(); break;
    case 'p': jumpPrevCompact(); e.preventDefault(); break;
    case 'f': if (!e.metaKey && !e.ctrlKey) {{ document.getElementById('search')?.focus(); e.preventDefault(); }} break;
  }}
}});

// Scroll tracking
let scrollTick = false;
window.addEventListener('scroll', () => {{
  if (!scrollTick) {{
    requestAnimationFrame(() => {{
      updateScrollUI();
      scrollTick = false;
    }});
    scrollTick = true;
  }}
}});

function updateScrollUI() {{
  const scrollTop = window.scrollY;
  const docHeight = document.body.scrollHeight - window.innerHeight;
  const pct = docHeight > 0 ? (scrollTop / docHeight) * 100 : 0;

  // Progress bar
  const bar = document.getElementById('scroll-bar');
  if (bar) bar.style.width = pct + '%';

  // Show/hide toolbar
  const toolbar = document.getElementById('nav-toolbar');
  if (toolbar) toolbar.classList.toggle('visible', sessionLoaded && scrollTop > 200);

  // Minimap viewport
  const vp = document.getElementById('minimap-vp');
  const mm = document.getElementById('minimap');
  if (vp && mm && sessionLoaded) {{
    const mmH = mm.offsetHeight;
    const vpH = Math.max(20, (window.innerHeight / document.body.scrollHeight) * mmH);
    const vpTop = (pct / 100) * (mmH - vpH);
    vp.style.top = vpTop + 'px';
    vp.style.height = vpH + 'px';
  }}

  // Position indicator
  if (sessionLoaded) {{
    const entries = document.querySelectorAll('#entries > .entry:not(.filtered-out)');
    const total = entries.length;
    if (total > 0) {{
      const scrollY = window.scrollY + window.innerHeight / 3;
      let current = 0;
      for (let i = entries.length - 1; i >= 0; i--) {{
        if (entries[i].offsetTop <= scrollY) {{ current = i + 1; break; }}
      }}
      const ind = document.getElementById('pos-indicator');
      if (ind) {{
        ind.textContent = current + ' / ' + total;
        ind.classList.add('visible');
      }}
    }}
  }}
}}

// Build minimap dots
function buildMinimap() {{
  const mm = document.getElementById('minimap');
  if (!mm) return;
  // Remove old dots
  mm.querySelectorAll('.minimap-dot').forEach(d => d.remove());

  const entries = document.querySelectorAll('#entries > .entry');
  const total = entries.length;
  if (total === 0) {{ mm.classList.remove('visible'); return; }}
  mm.classList.add('visible');

  const mmH = mm.offsetHeight;
  entries.forEach((el, i) => {{
    const dot = document.createElement('div');
    dot.className = 'minimap-dot';
    const y = (i / total) * mmH;
    dot.style.top = y + 'px';
    const t = el.dataset.type;
    const isCompact = el.classList.contains('compact');
    if (isCompact) dot.classList.add('mm-compact');
    else if (t === 'user') dot.classList.add('mm-user');
    else if (t === 'assistant') dot.classList.add('mm-assistant');
    else if (t === 'tool') dot.classList.add('mm-tool');
    else {{ dot.style.background = '#30363d'; }}
    mm.appendChild(dot);
  }});
}}

// Minimap click to scroll
document.getElementById('minimap')?.addEventListener('click', e => {{
  const mm = document.getElementById('minimap');
  const rect = mm.getBoundingClientRect();
  const pct = (e.clientY - rect.top) / rect.height;
  window.scrollTo({{ top: pct * (document.body.scrollHeight - window.innerHeight), behavior: 'smooth' }});
}});

// Patch loadSession to set flag and build minimap
const _origLoadSession = loadSession;
loadSession = async function(filepath, el) {{
  await _origLoadSession(filepath, el);
  sessionLoaded = true;
  apiViewLoaded = false;
  setTimeout(buildMinimap, 100);
  updateScrollUI();
}};

loadFolders();
</script>
</body>
</html>"""


def main():
    server = http.server.HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Session Viewer running at http://127.0.0.1:{PORT}")
    threading.Timer(0.5, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}")).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()


if __name__ == "__main__":
    main()
