import uuid

from sqlmodel import Field, SQLModel

from app.models.common import CreatedAtMixin, UUIDPrimaryKeyMixin


class PipelineBase(SQLModel):
    business_id: uuid.UUID
    name: str


class PipelineCreate(PipelineBase):
    pass


class PipelineRead(PipelineBase):
    id: uuid.UUID
    created_at: CreatedAtMixin.__annotations__["created_at"]


class Pipeline(PipelineBase, UUIDPrimaryKeyMixin, CreatedAtMixin, table=True):
    __tablename__ = "pipelines"

    business_id: uuid.UUID = Field(foreign_key="businesses.id", index=True)


class PipelineStageBase(SQLModel):
    pipeline_id: uuid.UUID
    name: str
    position: int
    color: str


class PipelineStageCreate(PipelineStageBase):
    pass


class PipelineStageRead(PipelineStageBase):
    id: uuid.UUID


class PipelineStage(PipelineStageBase, UUIDPrimaryKeyMixin, table=True):
    __tablename__ = "pipeline_stages"

    pipeline_id: uuid.UUID = Field(foreign_key="pipelines.id", index=True)
