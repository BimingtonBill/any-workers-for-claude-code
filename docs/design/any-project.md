# Workers in any project

The user's aim (2026-09-26): "anyone should be able to install deepseek workers and point it at any
project". The read-only kinds already worked anywhere; coders (`tools/ds_impl.ps1`) assumed Project A's
Rust layout, and an npm project's lead bypassed ds_impl for plain `-Mode edit` (another project, 2026-09-26).

## What changed

- **Kinds of project.** ds_impl detects Rust, Node, Python, Go and .NET from their manifest files and gives
  the coder that kind's usual test tools (`npm test`, `npm run *`, `npx tsc/vitest/jest/eslint`, `pytest`,
  `go test`, `dotnet test` ...), always plus python and **the brief's own acceptance commands exactly**, so a
  coder can run the check it is judged by whatever it is. `implAllowTools` still replaces the defaults.
- **Dependencies in the worktree.** A fresh worktree has no git-ignored folders, so `npm test` or a `.venv`
  interpreter failed there. Each of `node_modules`, `.venv`, `venv` (or the project's `worktreeLinks`) that
  exists and git ignores is linked in as a junction, with `denyEdit` on it. `-Discard` removes the links
  first (a non-recursive delete), so removing the worktree can't reach the real folder.
- **`local/` out of git.** ds_impl and the launcher add `/local/` to the clone's `.git/info/exclude` when the
  project doesn't ignore it: worktrees and run records no longer show as untracked files, and no tracked file
  changes.
- **A neutral coder template**, `templates/brief-impl.md`, replaces `brief-implementation.md`, which named
  Bevy, CARGO_TARGET_DIR and Project A's tasks folder and was used by no code.
- **Hook fix found on the way.** Splitting `T="C:/.../DeepSeek Workers/tools/ds_impl.ps1"` at the space made
  the hook read the tail as a direct call, so `-Integrate "$T"` was refused as a worker launch. Quotes inside a
  word now keep it whole.

## Evidence

Two fresh git projects in a scratch folder, with no `.deepseek-agents.json`, no `tasks/`, no ignore for
`local/`, run exactly as a new user would (`ds_impl.ps1 -Brief <file>` from the project folder):

- **Node** (`package.json`, `node --test`, a dependency only in the git-ignored `node_modules`): "added /local/
  to .git/info/exclude", "linked node_modules", the coder added `median` in 10 turns and ran `npm test` itself;
  scope ok, acceptance PASS. A retry was integrated: `npm test` in the main checkout passed 10 tests;
  `-Discard` left `node_modules` intact and `git status` showed only the integrated files and the brief.
- **Python** (`pyproject.toml`, a `.venv` the acceptance command uses, and a test that asserts it runs inside
  `.venv`): "linked .venv", the coder added `max_length` in 8 turns; scope ok, acceptance PASS (7 tests).
  `-Discard` left `.venv` intact.
- One integrate call inside a shell loop printed nothing and merged nothing; the same call alone worked and it
  did not recur.
- Tests: `tests/test_ds_impl.py` (a Node project is detected and linked; `-Discard` removes a link but never
  what it points to), `tests/test_ds_hook.py` (the quoted variable case).
