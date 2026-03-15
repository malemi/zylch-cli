---
description: Commit Session State to Documentation
---
1. Overcome chat context truncation by inspecting actual file changes. 
   - Run `git status` and `git diff` to review uncommitted work. 
   - If commits were made during this session, run `git log -n 5` or `git diff HEAD~1` to review recent changes.
2. Read `./docs/active-context.md` and `./docs/architecture.md` to establish the baseline before the changes.
3. Update `./docs/architecture.md` ONLY if structural changes, new dependencies, or new modules were introduced. Keep it as a declarative living document (no past tense, no changelogs).
4. Update `./docs/active-context.md`. Overwrite its contents to reflect:
   - The CURRENT state of the system based strictly on the git inspection.
   - What was genuinely completed in the codebase.
   - The immediate next steps or unresolved issues for the next session.
5. Update headers or content of other relevant `*.md` files if their scope changed.
6. Output only: `Session state committed based on codebase changes. Active context updated.`