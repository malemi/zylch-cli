---
description: Migrate Documentation to Split Context Structure
---
1. Read the existing `./docs/ARCHITECTURE.md` and any other relevant `*.md` files in `./docs/`.
2. Scan the source code directories (e.g., `src/`, `lib/`) and main configuration files (e.g., `package.json`, `docker-compose.yml`) to understand the current implementation and actual project structure.
3. Analyze both the old documentation and the real source code, then split the knowledge into three distinct files:
   - Create `./docs/system-rules.md`: Extract coding standards, tech stack, libraries, and strict formatting rules observed in the actual codebase.
   - Update/Overwrite `./docs/architecture.md`: Document the high-level system design, module boundaries, data flow, and infrastructure based on how the code is genuinely structured right now.
   - Create `./docs/active-context.md`: Summarize the current state of the project, recent features found in the code, and immediate next steps.
4. Ensure all three files are written using a declarative tone. They are living documents, not logs. No past tense, no historical references.
5. Output only: `Migration complete. New documentation structure generated.`