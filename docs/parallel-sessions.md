# Rules for every worker in this repo

Your brief has the task. This file has the rules. Read all of it.

## Working
- Work only in the worktree and branch your brief names, and only in the folders it says you own.
- Commit and push a WIP commit after each meaningful step. Never merge into `main`; the architect does that.
- After 2 failed attempts at the same problem, stop and report.
- Run long builds, benchmarks and tests in the background, send output to a file, read only the summary and failures.
- Prefer a script that can run unattended over doing a thing by hand: this laptop stays on, model time is the scarce thing.
- This machine is shared by several workers. Benchmarks that need a quiet machine take the lock first: `lockf ~/Developer/vcv-rivals/bench.lock <command>`.

## Contest rules we hold ourselves to (organizers read the top entries)
- Everything in this repo is our own work, MIT licensed. Organizers said in chat (2026-11-21 17:08 UTC) that teams that copy other teams may be disqualified.
- Other teams' repos live as bare copies in `~/Developer/vcv-rivals/`. They are test opponents and a source of ideas only.
  Never copy their code, files or generated tables into this repo. Never unpack them onto this disk: read with
  `git --git-dir=<repo>.git show HEAD:<path>` or `git --git-dir=<repo>.git grep`.
- Rival code is executed only inside a Docker container with `--network none`, a read-only root, and an unprivileged user. Never on the host.
- No answers looked up from a table keyed by the hidden data. Nothing downloaded at build time from an address we control.
- Never send team or personal names to an outside service. Public, unauthenticated contest pages only.
- Nobody but Nico submits. You prepare a commit and report its hash.

## Pull requests
- GraphQL is blocked here, so open a PR with the REST API:
  `gh api repos/ndunl075/<this-repo>/pulls -f title='...' -f head='<branch>' -f base='main' -f body='...'`
- Update an open branch by merging `main` in. Never rebase and force-push.
- Description: what changed, how it was tested (exact commands and results), files touched, open questions. No self-assessment.

## Review gate
A commit becomes a submission candidate only after a reviewer with no context has checked it against the track rules.
