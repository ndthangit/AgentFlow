"""Resolve allow-listed Agent runtimes and execute their container adapters."""

import json
import os
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from typing import Any, Protocol

from domain.errors import WorkflowExecutionError
from runtime.agent_container import AgentContainerExecutor, AgentContainerSettings

RUNTIME_ID_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
IMAGE_REFERENCE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:@-]{0,254}$")


class AgentRuntimeExecutor(Protocol):
    async def execute(
        self,
        request: dict[str, Any],
        *,
        environment_overrides: dict[str, str] | None = None,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class AgentRuntimeDefinition:
    id: str
    image: str


def _configuration_error(message: str) -> WorkflowExecutionError:
    return WorkflowExecutionError(message, code="CONFIGURATION_ERROR")


def _validate_definition(definition: AgentRuntimeDefinition) -> None:
    if not RUNTIME_ID_PATTERN.fullmatch(definition.id):
        raise _configuration_error(
            f"Invalid Agent runtime id in catalog: {definition.id!r}"
        )
    if not IMAGE_REFERENCE_PATTERN.fullmatch(definition.image):
        raise _configuration_error(
            f"Invalid image reference for Agent runtime {definition.id}"
        )


def runtime_definitions_from_env() -> tuple[AgentRuntimeDefinition, ...]:
    """Load the deploy allow-list, retaining the old single-image fallback."""
    raw_catalog = os.getenv("AGENT_RUNTIME_IMAGES", "").strip()
    default_runtime_id = os.getenv("AGENT_RUNTIME_DEFAULT", "default").strip()
    if not raw_catalog:
        legacy_image = os.getenv(
            "AGENT_RUNTIME_IMAGE", "agentflow-agent-default:0.1.0"
        ).strip()
        return (AgentRuntimeDefinition(default_runtime_id, legacy_image),)
    try:
        catalog = json.loads(raw_catalog)
    except json.JSONDecodeError as exc:
        raise _configuration_error("AGENT_RUNTIME_IMAGES must be valid JSON") from exc
    if not isinstance(catalog, dict) or not catalog:
        raise _configuration_error(
            "AGENT_RUNTIME_IMAGES must be a non-empty JSON object"
        )
    definitions: list[AgentRuntimeDefinition] = []
    for runtime_id, image in catalog.items():
        if not isinstance(runtime_id, str) or not isinstance(image, str):
            raise _configuration_error(
                "AGENT_RUNTIME_IMAGES keys and values must be strings"
            )
        definitions.append(AgentRuntimeDefinition(runtime_id, image))
    return tuple(definitions)


class AgentRuntimeManager:
    """Select a trusted runtime image and exchange one bounded JSON request."""

    def __init__(
        self,
        definitions: Iterable[AgentRuntimeDefinition],
        *,
        default_runtime_id: str = "default",
        container_settings: AgentContainerSettings | None = None,
        executor_factory: Callable[[AgentContainerSettings], AgentRuntimeExecutor]
        | None = None,
    ) -> None:
        self.default_runtime_id = default_runtime_id
        self._definitions: dict[str, AgentRuntimeDefinition] = {}
        for definition in definitions:
            _validate_definition(definition)
            if definition.id in self._definitions:
                raise _configuration_error(
                    f"Duplicate Agent runtime id in catalog: {definition.id}"
                )
            self._definitions[definition.id] = definition
        if not self._definitions:
            raise _configuration_error("At least one Agent runtime must be configured")
        if default_runtime_id not in self._definitions:
            raise _configuration_error(
                f"Default Agent runtime is not in the catalog: {default_runtime_id}"
            )
        self._container_settings = (
            container_settings or AgentContainerSettings.from_env()
        )
        self._executor_factory = executor_factory or AgentContainerExecutor
        self._executors: dict[str, AgentRuntimeExecutor] = {}

    @classmethod
    def from_env(
        cls,
        *,
        executor_factory: Callable[[AgentContainerSettings], AgentRuntimeExecutor]
        | None = None,
    ) -> "AgentRuntimeManager":
        default_runtime_id = os.getenv("AGENT_RUNTIME_DEFAULT", "default").strip()
        return cls(
            runtime_definitions_from_env(),
            default_runtime_id=default_runtime_id,
            executor_factory=executor_factory,
        )

    def available_runtime_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._definitions))

    def resolve(self, runtime_id: str) -> AgentRuntimeDefinition:
        selected_id = self.default_runtime_id if runtime_id == "agent" else runtime_id
        if not isinstance(selected_id, str) or not RUNTIME_ID_PATTERN.fullmatch(
            selected_id
        ):
            raise WorkflowExecutionError(
                f"Invalid Agent runtime id: {selected_id!r}",
                code="CONFIGURATION_ERROR",
            )
        definition = self._definitions.get(selected_id)
        if definition is None:
            raise WorkflowExecutionError(
                f"Agent runtime is not registered: {selected_id}",
                code="CONFIGURATION_ERROR",
            )
        return definition

    async def execute(
        self,
        runtime_id: str,
        request: dict[str, Any],
        *,
        environment_overrides: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        definition = self.resolve(runtime_id)
        executor = self._executors.get(definition.id)
        if executor is None:
            settings = replace(self._container_settings, image=definition.image)
            executor = self._executor_factory(settings)
            self._executors[definition.id] = executor
        return await executor.execute(
            request, environment_overrides=environment_overrides
        )
