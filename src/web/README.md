# AgentFlow Web

A minimal React/Vite interface for the FastAPI System. The web app signs in to
Keycloak with Authorization Code + PKCE, then lets users create workflows, edit
JSON drafts, validate and publish them, and create runs.

```powershell
npm.cmd install
npm.cmd run dev
```

By default, the web app runs at `http://localhost:5173`. The Docker build is exposed
at `http://localhost:3000`.

## Source structure

Code under `src` is grouped by responsibility so feature changes do not pull in
unrelated areas:

```text
src/
├── app/                 # App shell, navigation, and page-level state
├── features/
│   ├── mcp/             # MCP server management
│   ├── models/          # Provider and model management
│   └── workflows/       # Templates, summaries, and workflow editor
│       └── editor/      # Canvas, graph utilities, schema builder, and types
├── shared/              # Shared API client, authentication, and contracts
└── styles/              # Application CSS entry point
```

Pure graph transformation logic lives in `features/workflows/editor/graph.ts`;
React components only manage state and rendering. Sample workflows live separately
in `features/workflows/templates.ts` so demo data is not mixed into the app shell.

The workflow editor autosaves the draft after every valid graph operation. The save
button in the node drawer updates the graph and queues a database write; writes are
serialized by revision to prevent overwrites. Publishing waits for the latest draft
to finish saving before creating a version, so there is no separate “Save draft”
button.

Verify the production build after making changes:

```powershell
npm.cmd run build
```
