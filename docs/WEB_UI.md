# The web memory UI

A read-only, browser-facing view of the brain, served from the same Fly app as the hosted
MCP server (`oracle/agent/webui.py` + `web/`). Generic and data-driven: the public template
ships with no data and fills from whatever a person loads; a private deployment shows your
data behind your auth. Nothing personal is baked into the code.

## What it shows

| Tab | What it is |
|-----|------------|
| **Graph** | Obsidian-style force graph: wiki topics + the content they cite, with semantic edges you grow on double-click. A **Focus** toggle shows one node's neighborhood. |
| **Search** | Semantic + keyword retrieval, with an "explain how it was found" toggle. |
| **Wiki** | The self-compiled topic pages, with citations. |
| **Memory** | The agent's four memory kinds + an educational header: the record→recall→consolidate **lifecycle**, a **four-kinds** panel, and a **"one database, two shapes"** visual. |
| **Agents** | A registry of **everything built on the brain** — agents, playbooks, tools, jobs, sources, integrations, skills, schedules — auto-detected (see below). |
| **Feed** | Newest items. |
| **Overview** | "What's in my brain": by-source and by-type bars, series, coverage, memory counts, and the source-health panel. |

Structural privacy holds throughout: every read filters `visibility='content'`, so business/deal
data (content **and** agent memory) never appears — safe to demo on screen.

## The agents registry is auto-detected

Nobody maintains a list. `scripts/build_registry.py` scans the codebase — agent files, MCP
tools/playbooks (parsed from decorators), loaders, jobs, integrations, skills, launchd plists —
and writes:

- `web/registry.json` — the **generic** catalog (public repo, ships to everyone).
- `private/server/registry.private.json` — **your** private items (private repo, ships only on
  your deployment via the Dockerfile).

`oracle/agent/registry.py` reads and merges them; `/api/agents` serves the result. The **daily
sync regenerates it** (`scripts/sync.py`'s "Registry" step), so a new agent appears on its own.

To refresh by hand: `./.venv/bin/python scripts/build_registry.py`.

## Enabling / deploying

Env (fail-closed): `UI_ENABLED=1` turns it on; `UI_AUTH_TOKEN` (≥32 chars) gates `/api/*`;
`UI_PUBLIC_READ=1` is the explicit anonymous-read escape hatch for a public showcase.

```
fly secrets set UI_ENABLED=1 UI_AUTH_TOKEN="$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')" -a <app>
fly deploy --remote-only -c private/server/fly.toml   # private deploy (or deploy/Dockerfile for public)
```

App files (html/js/css) are served `no-cache` so a redeploy is never served stale; only the
version-pinned vendor lib is long-cached.

## Open items / next steps

Nothing here is blocking — the UI is complete and tested (`tests/test_brain.py`, green). These
are optional follow-ups:

- [ ] **Deploy to see it live.** The code is pushed but not yet deployed — redeploy the private
      Fly app (above) so the Overview dashboard, Agents registry, and memory visuals show on the
      hosted UI. Warm the machine before filming (scale-to-zero cold start).
- [ ] **Regenerate the registry before a deploy** if you added an agent since the last sync
      (`scripts/build_registry.py`, then commit). *Possible improvement:* run it during the Docker
      build so the shipped registry is always current without a manual step.
- [ ] **`script` skill isn't listed** — it has no `SKILL.md` under `private/claude-code/skills/`,
      so the scanner doesn't see it. Add one there and rerun the scanner to include it.
- [ ] **Filmable artifact is behind** — the shareable storyboard artifact has the Memory lifecycle
      but not the Overview dashboard or Agents registry. Mirror them only if you film the artifact
      instead of the real app.
- [ ] **Live agent console (future)** — watching a workflow *run* is a separate interactive surface,
      not this read-only view. Not built.
- [ ] **Confirm the parallel-DML fix is on `main`** — the `wiki._set_hwm` deadlock fix landed on a
      branch (`cbc0ded`); verify it's merged so the `test_set_hwm` flake can't recur.
