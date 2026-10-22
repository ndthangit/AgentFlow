# Managing Agent skills

The System manages a shared skill catalog. Every enabled skill automatically appears
in each Agent node's picker; users do not attach skills at the workflow level. The
Agent integration does not read entire folders and receives only skills whose IDs
are selected in the node's `config.skillIds`.

## Skill sources

| Source | Definition | Edit permissions |
| --- | --- | --- |
| Built-in | `src/system/skills/<slug>/SKILL.md` and `metadata.json` | Edit in the repository and redeploy the System |
| User | PostgreSQL `system.skills` | Only the owning JWT `sub` may update it |

At startup, the System reads built-in skills from the filesystem and upserts them
into PostgreSQL. If `SKILL.md` changes, the System increments the version and
updates the SHA-256 hash. Built-in skills cannot be edited through the API.

## API

| Endpoint | Function |
| --- | --- |
| `GET /v1/skills` | List built-in skills and the signed-in user's skills |
| `POST /v1/skills` | Create a user skill |
| `GET /v1/skills/{id}` | Read an accessible skill |
| `PUT /v1/skills/{id}` | Update a user skill with `expected_version` |

Legacy workflow-skill endpoints remain for old-client compatibility, but the UI and
publish process no longer use workflow-level selection. During validation and
publishing, the System verifies every `config.skillIds` value and accepts only
enabled built-in skills or skills owned by the current user.

## Publish-time snapshots

When publishing a workflow version, the System collects the IDs selected by all
Agent nodes, removes duplicates in encounter order, and copies these fields to
`workflow_versions.graph.skills`:

```json
{
  "id": "skill-uuid",
  "slug": "workflow-designer",
  "name": "Workflow Designer",
  "version": 1,
  "content_hash": "sha256",
  "instructions": "SKILL.md content at publish time"
}
```

The worker reads skills from the workflow-version snapshot and passes only the
running Agent's `skillIds`. Editing or disabling a skill later does not change older
versions or runs.

## Adding a built-in skill

Create two files:

```text
src/system/skills/my-skill/
├── metadata.json
└── SKILL.md
```

`metadata.json`:

```json
{
  "name": "My Skill",
  "description": "A short description that helps users decide whether to select the skill."
}
```

The folder name is the slug and may contain lowercase letters, digits, and hyphens.
`SKILL.md` contains the full instructions given to the agent. Never put API keys,
passwords, or credentials in a skill.
