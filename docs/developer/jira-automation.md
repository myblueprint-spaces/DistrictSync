# DSYNC Jira automation and ticket workflow

Source of truth for the Jira automation rules in project **DSYNC** and for how an agent creates and works a ticket. The rules live in Jira, not in the repo, so this file is how they are reviewed, recreated and kept honest. The release-side conventions (release ticket, fix versions, rollback) stay in [`release.md`](release.md#work-tracking-jira).

The DIH repo keeps the equivalent file at `docs/jira-automation/README.md` (data-insights-hub). DSYNC is a desktop tool with no deploy pipeline, so it uses the plain four-status board and three rules, not DIH's release-pipeline statuses. A shared cross-repo standard is later work.

**State:** the three rules were created on 2026-10-26. Confirm each fired correctly in its audit log after the first real ticket (see [Verify and troubleshoot](#verify-and-troubleshoot)) and update this line.

## Status order

Board: <https://myblueprint.atlassian.net/jira/software/projects/DSYNC/boards/238>

| Rank | Status | Moved by |
| --- | --- | --- |
| 0 | To Do | ticket creation |
| 1 | In Progress | rule 1 (a branch with the key is created) |
| 2 | In Review | rule 2 (a PR is opened) |
| 3 | Done | rule 3 (the PR merges) |
| (terminal) | Won't do | by hand; no rule touches it |

**Done means merged, not released.** Releasing is tracked on the release ticket and the `vX.Y.Z` fix version ([`release.md`](release.md#work-tracking-jira)).

Rules match status names as text. Rename a status only by changing this file, the Jira workflow and every rule together.

## Preconditions

- **GitHub for Jira** is connected to `myblueprint-spaces/DistrictSync`. Without it no branch, PR or merge event reaches Jira. A ticket's Development panel showing its PRs means it is connected.
- **The `DSYNC-<n>` key is in the branch name** (`DSYNC-123-short-summary`) **and the PR title** (`DSYNC-123 type(scope): summary`). The rules move only the ticket the key names.

## Common settings for every rule

- **Scope:** project DSYNC only.
- **Owner:** a named person, with "E-mail flow owner once when flow starts failing" on, so a broken rule is noticed.
- **Who can edit:** All admins (not Private), so a rule survives its creator leaving.
- **Description:** point back to this file ("Spec: DistrictSync repo, docs/developer/jira-automation.md").

Rules 1 and 3 started from Atlassian's Developer tools templates; rule 2 was built from scratch. Names below are the names in Jira.

## Rule 1: "When a branch is created → then move issue to in progress"

Trigger: **Branch created.**

Condition: Work item fields: Status equals **To Do**.

Action: Transition the work item to **In Progress**.

## Rule 2: "When a pull request is created → then move work item to in review"

Trigger: **Pull request created.**

Condition: Work item fields: Status is one of **To Do, In Progress**.

Action: Transition the work item to **In Review**.

## Rule 3: "When a pull request is merged → then move work item to done"

Trigger: **Pull request merged.**

Condition: Work item fields: Status does not equal **Done**.

Action: Transition the work item to **Done**.

## Why the guards exist

| Guard | Why |
| --- | --- |
| Status conditions | A second branch, a second PR or a re-run never reopens or moves a ticket backwards. A PR on an already-Done ticket changes nothing. |
| Rule 3 does not require a status | A PR merged from To Do or In Progress still reaches Done, so a missed earlier rule does not strand the ticket. |

## Manual by design

- **Fix Version** and marking the Jira version Released are set on the release ticket by hand ([`release.md`](release.md#work-tracking-jira)).
- **Won't do** and epics are closed by hand.
- **A PR that closes more than one ticket:** only the keyed ticket moves; move the rest by hand.

## Agent ticket workflow

Every change has a ticket **before** the branch exists, because rule 1 fires on branch creation and every rule matches the key.

1. **Create the ticket first** in project **DSYNC** (site `myblueprint.atlassian.net`). Issue type **Task**, or **Bug** for a defect. Summary: a short plain sentence naming the outcome. Body:

   ```
   ## Context
   Why this is needed, the evidence, and who asked.

   ## Change
   What will change, with the files or area.

   ## Acceptance criteria
   * Testable outcomes, including the gates that must pass.
   ```

   Release tickets use the template in [`release.md`](release.md#release-ticket). District and SIS onboarding is **not** a DSYNC ticket; it goes on the TO board.
2. **Branch** `DSYNC-<n>-short-summary` from `main`.
3. **Commit** messages start with the key: `DSYNC-<n> type(scope): summary`.
4. **Open the PR** titled `DSYNC-<n> type(scope): summary` and fill the template's Jira field.
5. **Do not move the ticket's status.** Rule 1 moves it to In Progress, rule 2 to In Review and rule 3 to Done. If a status is wrong, check the rule's audit log rather than moving it by hand, and report a rule that did not fire.
6. **Do not edit a ticket another person owns** beyond comments; set the assignee on tickets you create when the requester is known.

## Verify and troubleshoot

Each rule's **Audit log** (project DSYNC, Space settings, Automation, then the rule's Audit log, or the Audit log tab for all rules) shows why it did or did not fire. Common causes of a ticket that did not move:

- No `DSYNC-` key in the branch name (rule 1) or the PR title (rules 2 and 3).
- GitHub for Jira is not connected to the repo, or the PR never appeared in the ticket's Development panel.
- The ticket was already past the rule's status condition (for example, already In Review when the PR opened).
- The branch was created before the ticket existed, or the key was misspelled.
- The rule is disabled or the owner's account was deactivated.

## Restore from scratch

If the rules are lost or broken, recreate each of the three from the sections above:

1. Open project DSYNC, Space settings, Automation, then **Create flow**.
2. For rules 1 and 3 choose **Create from template**, search "branch is created" or "pull request is merged" (Developer tools). Rule 2 has no template: **Create from scratch**, trigger **Pull request created**.
3. Add or confirm the **condition** and the **Transition work item** action exactly as listed, and rename the flow to the heading above.
4. Open the flow's details: set the description, owner, error email and edit access (see [Common settings](#common-settings-for-every-rule)).
5. **Save and enable**, then check the Audit log after the next real branch or PR.

If any rule or status changes, change this file in a PR first, then apply it in Jira, then record it in [`../DECISIONS.md`](../DECISIONS.md).
