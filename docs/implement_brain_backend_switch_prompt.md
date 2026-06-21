# Implementation Prompt: Brain Backend Switch (Claude API ⇄ Local/Ollama)

> Paste this into Claude Code CLI in `C:\Projects\Claudia\`. Read each existing
> file before editing it. Match `CLAUDE.md` conventions. Confirm after each
> step before proceeding to the next.

---

Read `docs/brain_backend_switch.md` first — it has the full spec, all file
contents, and rationale. Implement in this exact order:

1. **Read `core/brain.py`** as it currently exists. Note its current
   single-provider implementation so the refactor preserves any
   project-specific logic not shown in the spec (e.g. logging setup,
   existing error handling patterns already established elsewhere in the
   codebase).

2. **Create `core/backends/__init__.py`** (empty, makes it a package) and
   **`core/backends/base.py`** — the abstract `BrainBackend` class, exactly
   as specified in `docs/brain_backend_switch.md`.

3. **Create `core/backends/claude_backend.py`** — move the existing Claude
   API logic out of `brain.py` into this file, conforming to `BrainBackend`.
   Preserve any existing retry/backoff logic already in `brain.py` if
   present — the spec's version is a clean baseline, not a mandate to drop
   existing resilience code.

4. **Create `core/backends/local_backend.py`** — new Ollama integration, as
   specified. Confirm `httpx` is already a dependency (it should be, from
   the internet research module) before adding it again.

5. **Update `config.yaml`** — add the `brain:` block from the spec. If a
   `brain:` key already exists with different structure, merge carefully
   and flag any conflicts to me rather than silently overwriting.

6. **Refactor `core/brain.py`** into the thin dispatcher shown in the spec.
   Keep the existing JARVIS/Claudia system prompt content that's already in
   the codebase — don't replace it with the placeholder text in the spec's
   code sample, that's just illustrative.

7. **Update `core/assistant.py`** — add the `_check_provider_switch()` method
   and wire it in before intent routing, as specified. Show me the exact
   insertion point in the existing orchestration method before applying it.

8. **Update `requirements.txt`** — add the comment noting Ollama is an
   external dependency, not pip-installed.

9. **Update `main.py`** boot sequence — add the backend status log line.

10. **Update `CLAUDE.md`** — add a line in the architecture/skill table
    pointing to `docs/brain_backend_switch.md`, following the same pattern
    used for the internet research module entry.

After each file, confirm what was changed and what's next. Do not proceed to
implementation step N+1 until I've reviewed step N.

---

## Manual setup (do this yourself, not via Claude Code)

Before testing, outside the codebase:

```
1. Install Ollama: https://ollama.com/download
2. ollama pull qwen2.5:7b-instruct
3. curl http://localhost:11434/api/tags   (verify it's serving)
```

---

## Test plan once implemented

1. Boot Claudia with `brain.active_provider: "claude"` in config — confirm
   boot log shows Claude active, both backends' reachability status.
2. Ask a normal question — confirm Claude answers as before (no regression).
3. Say "switch to local" — confirm spoken confirmation, then ask the same
   question — compare response time and quality side by side.
4. Say "switch to claude" — confirm it switches back.
5. Stop Ollama, say "switch to local" — confirm graceful failure message,
   no crash, Claudia stays on Claude.
6. With local active, trigger a research query (per `internet_research.md`)
   — confirm the `[LIVE WEB DATA]` injection still works and local backend
   doesn't choke on it (check `_budget_context` is trimming correctly via
   debug logs).
