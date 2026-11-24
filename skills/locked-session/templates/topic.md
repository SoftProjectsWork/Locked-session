Topic: {{topic}}
Status: WAITING — {{date}}
Since: {{date}} ({{timezone}})
Resume when: <condition>
Action required from the user: <one step, or —>

# Next step — <task ID>: <title>

## Context
<where things stand, in a few lines; [V] verified this session, [?] taken from a document>

## Good to know
<constraints, pitfalls, lessons>

## What to do
1. <step>

## Acceptance criteria
<how to tell it is done>

## User answers
- <date time>: <answer>

---

## Template
Every session on this topic rewrites the part above without piling up old blocks (history lives in git).
The `Status:` line is the topic's lock: `IN PROGRESS` while a session works on it, `WAITING` or `STOPPED` at the end
of a session, always with date and time `YYYY-MM-DD HH:MM`. Decisions go to the decision log.
