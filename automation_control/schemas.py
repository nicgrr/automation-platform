from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .models import ApprovalStatus, JobStatus


class HealthResponse(BaseModel):
    status: str = "ok"


class ApprovalCreate(BaseModel):
    requested_by: str
    action: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ApprovalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    requested_at: datetime
    requested_by: str
    action: str
    status: ApprovalStatus


class JobCreate(BaseModel):
    job_type: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class JobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    job_type: str
    status: JobStatus
    requested_at: datetime

