"""Contracts and validation for remote MCP server registrations."""

import re
import uuid
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
HEADER_PATTERN = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
FORBIDDEN_HEADERS = {"content-length", "host", "transfer-encoding"}


def validate_remote_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("MCP URL must use http or https")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("MCP URL cannot contain credentials or a fragment")
    return value.rstrip("/")


def validate_headers(value: dict[str, str] | None) -> dict[str, str] | None:
    if value is None:
        return None
    if len(value) > 20:
        raise ValueError("MCP headers cannot contain more than 20 entries")
    normalized: dict[str, str] = {}
    for raw_name, raw_value in value.items():
        name = raw_name.strip()
        header_value = raw_value.strip()
        if not name or not HEADER_PATTERN.fullmatch(name):
            raise ValueError("MCP header name is invalid")
        if name.lower() in FORBIDDEN_HEADERS:
            raise ValueError(f"MCP header {name} is not allowed")
        if not header_value or len(header_value) > 2000:
            raise ValueError("MCP header value must contain 1 to 2000 characters")
        if "\r" in header_value or "\n" in header_value:
            raise ValueError("MCP header value cannot contain line breaks")
        normalized[name] = header_value
    return normalized


class McpServerCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    slug: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=500)
    transport: Literal["streamable_http", "sse"] = "streamable_http"
    url: str = Field(min_length=8, max_length=2048)
    headers: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True

    @field_validator("slug")
    @classmethod
    def valid_slug(cls, value: str) -> str:
        if not SLUG_PATTERN.fullmatch(value):
            raise ValueError("slug must contain lowercase letters, numbers and hyphens")
        return value

    @field_validator("url")
    @classmethod
    def valid_url(cls, value: str) -> str:
        return validate_remote_url(value)

    @field_validator("headers")
    @classmethod
    def valid_headers(cls, value: dict[str, str]) -> dict[str, str]:
        return validate_headers(value) or {}


class McpServerUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    expected_revision: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=500)
    transport: Literal["streamable_http", "sse"]
    url: str = Field(min_length=8, max_length=2048)
    headers: dict[str, str] | None = None
    enabled: bool

    @field_validator("url")
    @classmethod
    def valid_url(cls, value: str) -> str:
        return validate_remote_url(value)

    @field_validator("headers")
    @classmethod
    def valid_headers(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        return validate_headers(value)


class McpServerView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    slug: str
    name: str
    description: str
    transport: Literal["streamable_http", "sse"]
    url: str
    has_headers: bool
    enabled: bool
    revision: int
    created_at: datetime
    updated_at: datetime
