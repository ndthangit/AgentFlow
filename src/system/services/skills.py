"""Skill catalog schemas and built-in filesystem synchronization."""

import hashlib
import json
import re
import uuid
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from domain.models import Skill

BUILTIN_OWNER = "__system__"
SKILLS_PATH = Path(__file__).resolve().parents[2] / "skills"
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def content_hash(instructions: str) -> str:
    return hashlib.sha256(instructions.encode("utf-8")).hexdigest()


class SkillCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    slug: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=500)
    instructions: str = Field(min_length=1, max_length=100_000)

    @field_validator("slug")
    @classmethod
    def valid_slug(cls, value: str) -> str:
        if not SLUG_PATTERN.fullmatch(value):
            raise ValueError("slug must contain lowercase letters, numbers and hyphens")
        return value


class SkillUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    expected_version: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=500)
    instructions: str = Field(min_length=1, max_length=100_000)
    enabled: bool = True


class SkillView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    slug: str
    name: str
    description: str
    instructions: str
    version: int
    content_hash: str
    enabled: bool
    source: str
    created_at: datetime
    updated_at: datetime


class WorkflowSkillSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    skill_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)

    @field_validator("skill_ids")
    @classmethod
    def unique_ids(cls, value: list[uuid.UUID]) -> list[uuid.UUID]:
        if len(set(value)) != len(value):
            raise ValueError("skill_ids must be unique")
        return value


async def sync_builtin_skills(session: AsyncSession) -> None:
    if not SKILLS_PATH.exists():
        return
    seen: set[str] = set()
    for metadata_path in SKILLS_PATH.glob("*/metadata.json"):
        skill_file = metadata_path.with_name("SKILL.md")
        if not skill_file.is_file():
            continue
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        instructions = skill_file.read_text(encoding="utf-8").strip()
        slug = metadata_path.parent.name
        if not SLUG_PATTERN.fullmatch(slug):
            raise ValueError(f"Invalid built-in skill folder name: {slug}")
        seen.add(slug)
        digest = content_hash(instructions)
        skill = await session.scalar(
            select(Skill).where(
                Skill.owner_subject == BUILTIN_OWNER, Skill.slug == slug
            )
        )
        if skill is None:
            session.add(
                Skill(
                    owner_subject=BUILTIN_OWNER,
                    slug=slug,
                    name=metadata["name"],
                    description=metadata.get("description", ""),
                    instructions=instructions,
                    content_hash=digest,
                    source="builtin",
                )
            )
        elif skill.content_hash != digest:
            skill.name = metadata["name"]
            skill.description = metadata.get("description", "")
            skill.instructions = instructions
            skill.content_hash = digest
            skill.version += 1
            skill.enabled = True
    existing = await session.scalars(
        select(Skill).where(Skill.owner_subject == BUILTIN_OWNER)
    )
    for skill in existing:
        if skill.slug not in seen:
            skill.enabled = False
    await session.commit()
