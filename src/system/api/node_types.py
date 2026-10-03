"""Catalog of workflow node types accepted by this System version."""

from fastapi import APIRouter

from api.dependencies import Claims
from domain.node_registry import node_type_catalog
from domain.schemas import NodeTypeView

router = APIRouter(prefix="/v1/node-types", tags=["node-types"])


@router.get("", response_model=list[NodeTypeView])
async def list_node_types(_claims: Claims):
    return node_type_catalog()
