# Plans

Written plans for work big enough that starting to code without one is a mistake.

## When to write one

Write a plan first when the work touches more than two modules, changes an interface other code depends on, or when you and the requester might not mean the same thing by the request. Skip it for a bug fix, a threshold change, or anything a roadmap checkbox already describes precisely.

If you're unsure, the tell is this: can you state the exit criterion in one sentence? If yes, just do it. If the answer is "well, it depends what we decide about…", write the plan.

## Convention

One file per plan: `YYYY-MM-DD-short-name.md`.

Cover, in this order:

1. **What we're building and why** — in plain language, at the level someone non-technical can check. If the plan is wrong, this is the section where it'll show.
2. **What it touches** — the modules and interfaces involved.
3. **Approach** — including the alternatives considered and why they lost. Anything genuinely rejected also belongs in `docs/decisions.md`.
4. **How we'll know it works** — the test or the corpus measurement, named specifically.
5. **What's explicitly not in this plan** — the scope fence.

## Lifecycle

Plans are written before the work and left in place afterward as a record of the reasoning. **Don't delete a plan when it ships** — mark its status at the top (`Status: implemented 2026-08-14`). A plan that was abandoned is worth even more: mark it `Status: abandoned` with a sentence on why, and add a `docs/decisions.md` entry so the idea doesn't come back around in three months.
