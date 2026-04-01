---
description: Merge current PR and clean up branches
allowed-tools: Bash(git *), Bash(gh *), Bash(sleep *)
---

# Merge PR and Clean Up

End-to-end workflow for merging the current branch's PR and cleaning
up local and remote branches.

## 1. Get PR Status

```
gh pr view --json number,title,state,statusCheckRollup,reviewDecision,url
```

If no PR exists for the current branch, stop and say so.

## 2. Check CI Status

```
gh pr checks
```

If checks are still running, poll every 30 seconds until all pass or
one fails:

```
while true; do
  STATUS=$(gh pr checks --json name,state --jq '[.[] | select(.state != "SUCCESS" and .state != "SKIPPED")] | length')
  FAILED=$(gh pr checks --json name,state --jq '[.[] | select(.state == "FAILURE")] | length')
  if [ "$FAILED" -gt 0 ]; then
    echo "❌ One or more checks failed:"
    gh pr checks --json name,state --jq '.[] | select(.state == "FAILURE") | "\(.name): \(.state)"'
    break
  fi
  if [ "$STATUS" -eq 0 ]; then
    echo "All checks passed."
    break
  fi
  PENDING=$(gh pr checks --json name,state --jq '[.[] | select(.state == "PENDING" or .state == "QUEUED" or .state == "IN_PROGRESS")] | length')
  echo "⏳ $PENDING checks still running... (polling in 30s)"
  sleep 30
done
```

If any check fails, stop and report which ones. Do not merge.

## 3. Confirm and Merge

If all checks pass, show the PR title and ask me to confirm before
merging.

Then merge with squash:

```
gh pr merge --squash --delete-branch
```

If the PR requires review approval and does not have it, report that
and stop.

## 4. Post-Merge Cleanup

```
git checkout main
git pull origin main
git fetch --all --prune --tags
```

Delete the local feature branch:

```
git branch -d <branch-name>
```

Verify the remote branch was auto-deleted:

```
git branch -r | grep <branch-name>
```

If the remote branch still exists, offer to delete it:

```
gh api -X DELETE repos/{owner}/{repo}/git/refs/heads/<branch-name>
```

## 5. Report

Summarize:
- Merged PR number and title
- New HEAD commit on main (hash and message)
- Local and remote branches cleaned up (or any that remain)
