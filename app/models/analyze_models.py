from pydantic import BaseModel
from typing import Optional, Literal


class AnalyzeRequest(BaseModel):
    text: str
    github_url: Optional[str] = None


class AnalyzeResponse(BaseModel):
    action_type: Literal["cicd", "k8s_api", "unknown"]
    summary: str
    confidence: float
    details: dict
