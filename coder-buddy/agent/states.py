from typing import Optional
from pydantic import BaseModel, Field, ConfigDict, model_validator


_VALID_EXTS = {
    ".html", ".css", ".js", ".ts", ".jsx", ".tsx", ".vue",
    ".py", ".json", ".md", ".txt", ".yaml", ".yml", ".env",
}


class File(BaseModel):
    path: str = Field(description="The path to the file")
    purpose: str = Field(default="", description="What this file does")

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, v):
        if isinstance(v, dict):
            # accept 'description' or 'summary' as aliases for 'purpose'
            if not v.get("purpose"):
                v["purpose"] = v.pop("description", None) or v.pop("summary", None) or ""
        return v


class Plan(BaseModel):
    name: str = Field(description="App name")
    description: str = Field(description="One-line description")
    techstack: str = Field(description="Comma-separated tech stack")
    features: list[str] = Field(description="List of features")
    files: list[File] = Field(description="Files to create")

    @model_validator(mode="after")
    def _clean_files(self):
        seen: set[str] = set()
        clean = []
        for f in self.files:
            # drop garbage files (config blobs, lint files, duplicates)
            ext = "." + f.path.rsplit(".", 1)[-1].lower() if "." in f.path else ""
            if f.path in seen or ext not in _VALID_EXTS:
                continue
            seen.add(f.path)
            clean.append(f)
        self.files = clean
        return self


class ImplementationTask(BaseModel):
    filepath: str = Field(description="Path of the file to modify")
    task_description: str = Field(description="Detailed description of what to implement")

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, v):
        if isinstance(v, dict):
            # accept 'description' or 'task' as aliases
            if not v.get("task_description"):
                v["task_description"] = (
                    v.pop("description", None)
                    or v.pop("task", None)
                    or "Implement this file"
                )
            if not v.get("filepath"):
                v["filepath"] = v.pop("file", None) or v.pop("path", None) or ""
        return v


class TaskPlan(BaseModel):
    implementation_steps: list[ImplementationTask] = Field(
        description="Ordered list of files to implement"
    )
    model_config = ConfigDict(extra="allow")


class CoderState(BaseModel):
    task_plan: TaskPlan = Field(description="The implementation plan")
    current_step_idx: int = Field(0, description="Index of current step")
    current_file_content: Optional[str] = Field(None, description="Content being edited")


class PatchTask(BaseModel):
    filepath: str = Field(description="File to modify")
    change_description: str = Field(description="Exactly what to add, change, or remove")

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, v):
        if isinstance(v, dict):
            if not v.get("change_description"):
                v["change_description"] = (
                    v.pop("description", None)
                    or v.pop("change", None)
                    or "Apply the requested change"
                )
            if not v.get("filepath"):
                v["filepath"] = v.pop("file", None) or v.pop("path", None) or ""
        return v


class PatchPlan(BaseModel):
    tasks: list[PatchTask] = Field(description="Files to patch and what to do in each")
    summary: str = Field(default="", description="One-line summary of all changes")
