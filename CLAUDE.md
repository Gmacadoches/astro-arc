# Astro-Arc

Changes go on the `development` branch, not `master`.

Checks: `backend/bin/astro-arc-check` (free; the pre-commit hook and CI run it). It pins the Stage 2 prompts and the HTTP requests; a failing pin names the command that re-pins it, for a change you meant.

Architecture dossier: `CONTEXT.md`, on this machine only (gitignored). Read the section for what you're changing: the pipeline stages, theme generation, history and saving, the settings UI, key storage, data files.

## Agent skills

### Issue tracker

Issues live in GitHub Issues on Gmacadoches/astro-arc, managed with the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Uses the five default labels: needs-triage, needs-info, ready-for-agent, ready-for-human, wontfix. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `GLOSSARY.md` at the root, with decision records in `docs/decisions/`. See `docs/agents/domain.md`.
