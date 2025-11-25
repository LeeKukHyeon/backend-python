from fastapi import FastAPI
from pydantic import BaseModel
from app.services.natural_command import parse_command


app = FastAPI(title="K8s AI Manager")

class NaturalRequest(BaseModel):
    text: str

@app.post("/natural/analyze")
async def natural_analyze(req: NaturalRequest):
    """
    사용자 입력(text)을 GPT 분석 로직으로 넘기고 결과 반환
    """
    result = await parse_command(req.text)
    return result