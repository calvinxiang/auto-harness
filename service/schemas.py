from datetime import datetime
from typing import Any, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .tasks import TASKS


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


class OrganizationCreate(Input):
    name: str = Field(min_length=1, max_length=100)
    owner_name: str = Field(min_length=1, max_length=100)


class MemberCreate(Input):
    name: str = Field(min_length=1, max_length=100)
    role: Literal['admin', 'member'] = 'member'


class MembershipUpdate(Input):
    role: Literal['admin', 'member']


class JobCreate(Input):
    task_ids: list[str] = Field(default_factory=lambda: list(TASKS), min_length=1, max_length=20)
    max_iterations: int = Field(default=2, ge=0, le=5, description='Proposals after baseline; 0 runs only baseline.')

    @model_validator(mode='after')
    def selected_tasks(self):
        if len(self.task_ids) != len(set(self.task_ids)):
            raise ValueError('Duplicate task IDs are not allowed')
        if set(self.task_ids) - TASKS.keys():
            raise ValueError('Unknown task ID; use GET /tasks for the supported subset')
        return self


class JobOut(BaseModel):
    id: UUID
    org_id: UUID
    user_id: UUID
    status: Literal['queued','running','succeeded','failed','cancelled']
    request: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    attempts: int
    next_iteration: int
    best_iteration_id: UUID | None
    best_score: float | None
    stop_reason: str | None
    error: dict[str, Any] | None


class TaskResultOut(BaseModel):
    task_id: str
    status: Literal['passed', 'failed', 'timeout', 'error']
    reward: float | None = Field(default=None, ge=0, le=1)
    failure_summary: str | None = None
    trace: str = ''
    agent_metadata: str | None = None
    verifier_output: str = ''


class BenchmarkResultsOut(BaseModel):
    dataset: str = 'terminal-bench@2.0'
    tasks: list[TaskResultOut]
    score: float = Field(ge=0, le=1)
    passed: int = 0
    failed: int = 0
    errors: int = 0
    runner_exit_code: int | None = None
    runner_log: str | None = None
    agent_model: str | None = None
    sandbox: str | None = None


class IterationOut(BaseModel):
    id: UUID
    job_id: UUID
    number: int
    attempt: int
    status: Literal['running', 'completed', 'interrupted', 'failed']
    agent_source: str
    agent_code: str | None = None
    source_diff: str | None = None
    validation: dict[str, Any] | None = None
    source_sha256: str
    prompt: str
    proposal: dict[str, Any] | None
    results: BenchmarkResultsOut | None
    score: float | None
    accepted: bool | None
    error: dict[str, Any] | None
    created_at: datetime
    finished_at: datetime | None


class Proposal(Input):
    diagnosis: str = Field(min_length=1, max_length=4000)
    rationale: str = Field(min_length=1, max_length=4000)
    agent_code: str = Field(min_length=50, max_length=60000)
