"""Redis Streams queue contract for workflow run dispatch."""

import os
from dataclasses import dataclass
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import ResponseError

DEFAULT_STREAM = "agentflow:workflow-runs"
DEFAULT_GROUP = "workflow-workers"


@dataclass(frozen=True)
class RunQueueMessage:
    message_id: str
    run_id: str
    reclaimed: bool = False


class RedisRunQueue:
    def __init__(
        self,
        client: Redis,
        stream: str = DEFAULT_STREAM,
        group: str = DEFAULT_GROUP,
    ) -> None:
        self.client = client
        self.stream = stream
        self.group = group

    @classmethod
    def from_env(cls) -> "RedisRunQueue":
        client = Redis.from_url(
            os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=10,
        )
        return cls(
            client,
            stream=os.getenv("WORKFLOW_QUEUE_STREAM", DEFAULT_STREAM),
            group=os.getenv("WORKFLOW_QUEUE_GROUP", DEFAULT_GROUP),
        )

    async def ensure_group(self) -> None:
        try:
            await self.client.xgroup_create(
                self.stream, self.group, id="0-0", mkstream=True
            )
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def ping(self) -> bool:
        return bool(await self.client.ping())

    async def enqueue(self, run_id: str) -> str:
        return await self.client.xadd(
            self.stream,
            {"run_id": run_id},
            maxlen=int(os.getenv("WORKFLOW_QUEUE_MAXLEN", "10000")),
            approximate=True,
        )

    @staticmethod
    def _messages(response: Any, reclaimed: bool) -> list[RunQueueMessage]:
        messages: list[RunQueueMessage] = []
        if reclaimed:
            entries = response[1] if isinstance(response, (list, tuple)) else []
        else:
            entries = response[0][1] if response else []
        for message_id, fields in entries:
            run_id = fields.get("run_id") if isinstance(fields, dict) else None
            if run_id:
                messages.append(
                    RunQueueMessage(
                        message_id=str(message_id),
                        run_id=str(run_id),
                        reclaimed=reclaimed,
                    )
                )
        return messages

    async def read(
        self, consumer: str, *, block_ms: int = 5000, count: int = 1
    ) -> list[RunQueueMessage]:
        response = await self.client.xreadgroup(
            self.group,
            consumer,
            {self.stream: ">"},
            count=count,
            block=block_ms,
        )
        return self._messages(response, reclaimed=False)

    async def reclaim(
        self,
        consumer: str,
        *,
        min_idle_ms: int = 300_000,
        count: int = 10,
    ) -> list[RunQueueMessage]:
        response = await self.client.xautoclaim(
            self.stream,
            self.group,
            consumer,
            min_idle_ms,
            "0-0",
            count=count,
        )
        return self._messages(response, reclaimed=True)

    async def acknowledge(self, message_id: str) -> None:
        await self.client.xack(self.stream, self.group, message_id)

    async def remove_consumer(self, consumer: str) -> None:
        await self.client.xgroup_delconsumer(self.stream, self.group, consumer)

    async def close(self) -> None:
        await self.client.aclose()
