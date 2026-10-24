"""Small scheduler benchmark for one workflow with independent Agent nodes."""

import asyncio
import time

from runtime.engine import execute_workflow


def graph(max_parallel: int) -> dict:
    return {
        "settings": {"maxParallelNodes": max_parallel},
        "nodes": [
            {
                "id": f"agent_{index}",
                "type": "agent",
                "config": {"inputSchema": {}, "outputSchema": {}},
            }
            for index in range(4)
        ],
        "edges": [],
    }


async def measure(max_parallel: int, node_seconds: float = 0.2) -> float:
    async def agent(node, _inputs):
        await asyncio.sleep(node_seconds)
        return {"node": node["id"]}

    started = time.perf_counter()
    result = await execute_workflow(graph(max_parallel), {}, agent)
    if result.status != "succeeded":
        raise RuntimeError(f"benchmark workflow failed: {result}")
    return time.perf_counter() - started


async def measure_dependency_pipeline() -> float:
    pipeline_graph = {
        "settings": {"maxParallelNodes": 2},
        "nodes": [
            {
                "id": node_id,
                "type": "agent",
                "config": {"inputSchema": {}, "outputSchema": {}},
            }
            for node_id in ("fast", "slow", "after_fast")
        ],
        "edges": [{"from": "fast", "to": "after_fast"}],
    }
    delays = {"fast": 0.1, "slow": 0.4, "after_fast": 0.2}

    async def agent(node, _inputs):
        await asyncio.sleep(delays[node["id"]])
        return {"node": node["id"]}

    started = time.perf_counter()
    result = await execute_workflow(pipeline_graph, {}, agent)
    if result.status != "succeeded":
        raise RuntimeError(f"pipeline benchmark failed: {result}")
    return time.perf_counter() - started


async def main() -> None:
    sequential = await measure(1)
    parallel = await measure(4)
    pipelined = await measure_dependency_pipeline()
    speedup = sequential / parallel
    print(f"sequential_seconds={sequential:.3f}")
    print(f"parallel_seconds={parallel:.3f}")
    print(f"speedup={speedup:.2f}x")
    print(f"dependency_pipeline_seconds={pipelined:.3f}")
    print("old_wave_barrier_estimate_seconds=0.600")


if __name__ == "__main__":
    asyncio.run(main())
