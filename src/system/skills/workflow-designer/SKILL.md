# Workflow Designer

When designing a workflow:

1. Start with exactly one trigger that matches the request.
2. Give each node a single responsibility and clearly describe its input and output.
3. Add an approval before an action that affects an external system when the request requires human review.
4. Do not create cycles or unbounded fan-out.
5. End every active branch with an `end` node.
6. Use an `if` node with `true`/`false` edges for conditional branches; the unselected branch will be skipped.
7. Use a `parallel` node with at least two `parallel` port edges when independent branches can run concurrently.
8. Child nodes may receive the branching node's output directly or reference it explicitly with `$nodes.<id>.output.<field>`.
9. Use `llm.call` for tasks that require exactly one model call; use `agent` only when a skill or multi-step agent behavior is required.
10. In prompts and instructions, reference resolved node data with `{{input.field}}`; use `{{input}}` when the complete JSON input is required.
