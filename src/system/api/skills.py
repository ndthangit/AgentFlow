"""Skill catalog endpoints."""

import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError

from api.dependencies import Claims, DatabaseSession
from domain.models import Skill
from services.skills import (
    BUILTIN_OWNER,
    SkillCreate,
    SkillUpdate,
    SkillView,
    content_hash,
)

router = APIRouter(prefix="/v1/skills", tags=["skills"])


def visible_skill_filter(subject: str):
    return or_(Skill.owner_subject == subject, Skill.owner_subject == BUILTIN_OWNER)


@router.get("", response_model=list[SkillView])
async def list_skills(claims: Claims, session: DatabaseSession):
    result = await session.scalars(
        select(Skill)
        .where(visible_skill_filter(claims["sub"]), Skill.enabled.is_(True))
        .order_by(Skill.source, Skill.name)
    )
    return list(result)


@router.post("", response_model=SkillView, status_code=201)
async def create_skill(
    request: SkillCreate,
    claims: Claims,
    session: DatabaseSession,
):
    skill = Skill(
        owner_subject=claims["sub"],
        slug=request.slug,
        name=request.name,
        description=request.description,
        instructions=request.instructions,
        content_hash=content_hash(request.instructions),
        source="user",
    )
    session.add(skill)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=409, detail="Skill slug already exists"
        ) from None
    await session.refresh(skill)
    return skill


@router.get("/{skill_id}", response_model=SkillView)
async def get_skill(
    skill_id: uuid.UUID,
    claims: Claims,
    session: DatabaseSession,
):
    skill = await session.scalar(
        select(Skill).where(
            Skill.id == skill_id,
            visible_skill_filter(claims["sub"]),
        )
    )
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    return skill


@router.put("/{skill_id}", response_model=SkillView)
async def update_skill(
    skill_id: uuid.UUID,
    request: SkillUpdate,
    claims: Claims,
    session: DatabaseSession,
):
    result = await session.execute(
        update(Skill)
        .where(
            Skill.id == skill_id,
            Skill.owner_subject == claims["sub"],
            Skill.source == "user",
            Skill.version == request.expected_version,
        )
        .values(
            name=request.name,
            description=request.description,
            instructions=request.instructions,
            content_hash=content_hash(request.instructions),
            enabled=request.enabled,
            version=Skill.version + 1,
        )
        .returning(Skill)
    )
    skill = result.scalar_one_or_none()
    if skill is None:
        exists = await session.scalar(
            select(Skill.id).where(
                Skill.id == skill_id,
                Skill.owner_subject == claims["sub"],
                Skill.source == "user",
            )
        )
        if exists is None:
            raise HTTPException(status_code=404, detail="Editable skill not found")
        raise HTTPException(status_code=409, detail="Skill version conflict")
    await session.commit()
    return skill
