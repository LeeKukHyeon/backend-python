from pydantic import BaseModel
from typing import Optional

class NaturalCommandRequest(BaseModel):
    text: str

class NaturalCommandResponse(BaseModel):
    gpt_output: str
    ci_cd_status: str
    k8s_status: str
