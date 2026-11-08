# Contributing — Developer Workflow

Guide for module developers to fork, develop, and submit changes via pull request.

---

## 1. Fork the Repository

1. Navigate to the repository on GitHub.
2. Click **Fork** (top-right) to create a copy under your GitHub account.
3. Clone your fork locally:

```bash
git clone https://github.com/<your-username>/DI_Workshop.git
cd DI_Workshop
```

4. Add the upstream remote (the original repo):

```bash
git remote add upstream https://github.com/<org>/DI_Workshop.git
```

---

## 2. Set Up Your Branch

Always create a feature branch from the latest `main`:

```bash
git checkout main
git pull upstream main
git checkout -b feature/<module-name>
```

**Branch naming convention:**

| Type | Pattern | Example |
|------|---------|---------|
| New module | `feature/<module-name>` | `feature/module-06-real-time` |
| Bug fix | `fix/<short-description>` | `fix/module-03-typo` |
| Enhancement | `enhance/<short-description>` | `enhance/module-01-add-diagram` |

---

## 3. Work on Your Module

### Directory Structure

Place your module content under `workspace/di-in-a-day-lab/`:

```
workspace/di-in-a-day-lab/
├── Module-01-Fabric-Mirroring/
│   ├── fabric-mirroring-azure-sql-lab.md
│   └── *.ipynb
├── Module-02-Data-Movement/
│   └── data-movement-lab.md
├── Module-03-Orchestration/
│   └── Module-03-Orchestration.md
├── Module-04-Transformation/
│   └── Module-04-Transformation.md
├── Module-05-Monitoring-Telemetry.md
└── Module-XX-Your-Module/          ← your new module
    ├── your-module-lab.md
    └── supporting-notebooks.ipynb
```

### Guidelines

- Use Markdown (`.md`) for lab instructions.
- Use Jupyter notebooks (`.ipynb`) for interactive exercises.
- Reference shared infrastructure from [FabricInfra.md](FabricInfra.md) — do not duplicate setup steps.
- Keep file and folder names lowercase with hyphens (e.g., `my-module-lab.md`).
- Do not commit secrets, credentials, or personal paths.

### Commit Your Changes

```bash
git add .
git commit -m "Add Module-XX: <short description>"
```

Make small, focused commits. Each commit should represent a logical unit of work.

---

## 4. Keep Your Branch Up to Date

Before pushing, sync with upstream to avoid merge conflicts:

```bash
git fetch upstream
git rebase upstream/main
```

If conflicts arise, resolve them locally, then continue:

```bash
git add <resolved-files>
git rebase --continue
```

---

## 5. Push and Create a Pull Request

Push your branch to your fork:

```bash
git push origin feature/<module-name>
```

Then on GitHub:

1. Navigate to your fork.
2. Click **Compare & pull request** (or go to the upstream repo → **Pull requests** → **New pull request**).
3. Set **base** to `main` (upstream) and **compare** to your branch.
4. Fill in the PR template:

### PR Description Template

```
## Summary
Brief description of what this module covers.

## Changes
- Added Module-XX lab instructions
- Added supporting notebook(s)
- Updated README if applicable

## Testing
- [ ] All markdown renders correctly
- [ ] Notebook cells execute without errors
- [ ] No hardcoded credentials or personal paths
- [ ] Links to other modules/docs are valid
```

---

## 6. Code Review Process

| Step | Action | Who |
|------|--------|-----|
| 1 | PR submitted | Developer |
| 2 | Automated checks pass (if configured) | CI |
| 3 | Reviewer assigned | Maintainer |
| 4 | Feedback provided via PR comments | Reviewer |
| 5 | Developer addresses feedback and pushes updates | Developer |
| 6 | Approval given | Reviewer |
| 7 | PR merged into `main` | Maintainer |

### Responding to Review Feedback

```bash
# Make requested changes on your branch
git add .
git commit -m "Address review: <what changed>"
git push origin feature/<module-name>
```

The PR updates automatically — no need to open a new one.

---

## 7. After Merge

Once your PR is merged:

```bash
# Switch back to main and sync
git checkout main
git pull upstream main

# Delete your feature branch locally
git branch -d feature/<module-name>

# Delete remote branch on your fork
git push origin --delete feature/<module-name>
```

---

## Quick Reference

```
Fork → Clone → Branch → Develop → Commit → Push → PR → Review → Merge
```

| Command | Purpose |
|---------|---------|
| `git remote -v` | Verify remotes (origin = fork, upstream = original) |
| `git status` | Check working tree status |
| `git log --oneline -5` | View recent commits |
| `git diff` | Review unstaged changes |
| `git stash` | Temporarily shelve changes |
