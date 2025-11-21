from fastapi import FastAPI
from pydantic import BaseModel
from services.natural_command import parse_command

app = FastAPI(title="K8s AI Manager")

class NaturalRequest(BaseModel):
    text:str

@app.post("/natural/command")
async def natural_command(req: NaturalRequest):
    """자연어 명령 처리 엔드포인트"""
    return await parse_command(req.text)