# Filesystem MCP demo

A minimal MCP server for the AgentFlow demo. It provides three tools:

- `write_file`: writes a UTF-8 file under `/data`.
- `read_file`: reads a UTF-8 file under `/data`.
- `list_files`: lists files under `/data`.

Absolute paths, `..`, symlinks that escape `/data`, files larger than 1 MiB, and
directory listings over 200 entries are rejected.

When running with Docker Compose, register it on the MCP page with:

- Slug: `filesystem-demo`
- Transport: `Streamable HTTP`
- URL: `http://filesystem-mcp:8002/mcp`
- Authentication header: leave blank

Then select this MCP server in the Agent node configuration. Data is persisted in
the `agentflow_mcp_files` volume.

