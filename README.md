# Claude Code Session Analyzer

A local web-based viewer for browsing and analyzing [Claude Code](https://docs.anthropic.com/en/docs/claude-code) session logs (`.jsonl` files stored in `~/.claude/projects/`).

![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue)
![No Dependencies](https://img.shields.io/badge/dependencies-none-green)
![License: MIT](https://img.shields.io/badge/license-MIT-yellow)

## Features

### Chat View
- Color-coded entries: **User** (green), **Assistant** (blue), **Tool** (yellow), **Thinking** (gray), **System** (red)
- Filter by message type, full-text search
- Fold/unfold individual entries or all at once
- **Compact content detection** — automatically identifies compressed/summarized content:
  - Curator compression records (purple badge)
  - Context continuation summaries (red badge)
  - `/compact` command output (green badge)
  - Session summaries (blue badge)

### API View
- Reconstructs actual API call boundaries from session logs
- Shows **request → response** pairs for each API call
- Multi-turn server-side tool loops displayed as individual turns within a single API call
- Token usage breakdown: input, output, cache read, cache create
- Visual token bar and estimated cost calculation (based on [Anthropic public pricing](https://www.anthropic.com/pricing))

### Tool ID Linking
- Click any `tool_use` to jump to its matching `tool_result` (with double-blink highlight)
- Click any `tool_result` to jump back to its `tool_use`
- Works in both Chat View and API View

### Navigation
- Floating toolbar: Top / Bottom / Next User / Prev User / Next Compact / Prev Compact
- Keyboard shortcuts: `T` `B` `J` `K` `N` `P` `F`
- Scroll progress bar + minimap with color-coded dots
- Position indicator (current / total entries)

## Quick Start

```bash
# No dependencies required — uses only Python stdlib
python3 claude_session_viewer.py
```

Opens `http://127.0.0.1:18923` in your browser. Select a project folder and session from the sidebar.

## Configuration

Environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `CSV_PORT` | `18923` | Server port |
| `CSV_BASE_DIR` | `~/.claude/projects` | Path to Claude Code session logs |
| `CSV_TZ_OFFSET` | `8` | Timezone offset from UTC (e.g., `8` for UTC+8, `-5` for EST) |

```bash
# Example: custom port and US Eastern timezone
CSV_PORT=8080 CSV_TZ_OFFSET=-5 python3 claude_session_viewer.py
```

## macOS Desktop App (Optional)

Create a clickable `.app` on your desktop:

```bash
mkdir -p ~/Desktop/"Claude Session Viewer.app"/Contents/MacOS
cat > ~/Desktop/"Claude Session Viewer.app"/Contents/MacOS/launch << 'EOF'
#!/bin/bash
exec /usr/bin/python3 /path/to/claude_session_viewer.py
EOF
chmod +x ~/Desktop/"Claude Session Viewer.app"/Contents/MacOS/launch
```

## How It Works

Claude Code stores session logs as JSONL files in `~/.claude/projects/<project-folder>/`. Each line is a JSON record with types like `user`, `assistant`, `system`, `progress`, etc.

This tool:
1. Scans the project folders to list available sessions
2. Parses the JSONL records to reconstruct the conversation
3. Groups assistant messages by `message.id` to identify API call boundaries
4. Detects compressed/compact content using regex patterns
5. Renders everything as an interactive web UI served locally

### Understanding API View

Each API call to Claude is identified by a unique `message.id`. Within a single API call, Claude Code may execute a **server-side tool loop**:

```
Model outputs tool_use → Claude Code executes tool → feeds result back → Model continues
(all within one HTTP streaming connection, one message.id)
```

The API View shows each turn within this loop as a separate **REQUEST → RESPONSE** pair, making it clear which tool results the model saw before making its next decision.

## License

MIT
