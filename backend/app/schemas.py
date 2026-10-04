"""Pydantic request models for the API (shapes from docs/DESIGN.md)."""

from pydantic import BaseModel, ConfigDict, Field


class ConnectRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    db_type: str = "mysql"
    host: str = "localhost"
    port: int = 0
    username: str = ""
    password: str = ""
    database: str = ""
    db_schema: str | None = Field(default=None, alias="schema")
    service_name: str | None = None
    ssl: bool = False


class ScanStartRequest(ConnectRequest):
    tables: list[str] = []
