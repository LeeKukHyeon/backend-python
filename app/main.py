import os
from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
import httpx
import openai
from typing import Dict

app = FastAPI(title="K8s AI Manager GPT Chat")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DOCKERHUB_USERNAME = os.getenv("DOCKERHUB_USERNAME")
DOCKERHUB_PASSWORD = os.getenv("DOCKERHUB_PASSWORD")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

openai.api_key = OPENAI_API_KEY

# 세션 상태 저장
session_store: Dict[str, Dict] = {}


class ChatRequest(BaseModel):
    session_id: str  # 대화 세션 식별
    message: str  # 사용자 자연어 입력


def parse_github_url(message: str):
    import re
    match = re.search(r"https://github\.com/([a-zA-Z0-9_.-]+)/([a-zA-Z0-9_.-]+)", message)
    if match:
        return match.group(1), match.group(2)
    return None, None


async def check_dockerfile_exists(owner: str, repo: str) -> bool:
    url = f"https://api.github.com/repos/{owner}/{repo}/contents"
    headers = {"Authorization": f"Bearer {GITHUB_TOKEN}"}
    async with httpx.AsyncClient() as client:
        res = await client.get(url, headers=headers)
        if res.status_code != 200:
            return False
        files = res.json()
        return any(f["name"].lower() == "dockerfile" for f in files)


async def check_dockerhub_repo(repo: str) -> bool:
    url = f"https://hub.docker.com/v2/repositories/{DOCKERHUB_USERNAME}/{repo}/"
    headers = {"Authorization": f"JWT {DOCKERHUB_PASSWORD}"}
    async with httpx.AsyncClient() as client:
        res = await client.get(url, headers=headers)
        return res.status_code == 200


async def query_gpt(prompt: str) -> str:
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3
    )
    return response.choices[0].message.content


@app.post("/api/ci/chat")
async def ci_chat(req: ChatRequest):
    # 세션 상태 초기화
    session = session_store.get(req.session_id, {})

    # GitHub URL 추출
    owner, repo = parse_github_url(req.message)
    if owner and repo and ("github_url" not in session):
        session["github_url"] = f"https://github.com/{owner}/{repo}"
        session["dockerfile_exists"] = await check_dockerfile_exists(owner, repo)
        session["dockerhub_repo_exists"] = await check_dockerhub_repo(repo)
        session["stage"] = "init"

    # GPT 프롬프트 구성
    prompt = f"""
사용자 메시지: {req.message}
현재 세션 상태: {session}

대화형으로 안내하면서 단계별 진행:
1) Dockerfile 생성 여부 질문
2) Dockerfile 예제 코드 생성
3) GitHub Action workflow 안내
4) Docker Hub 이미지 푸시 안내

사용자에게 단계별 선택지를 제시하고, 코드 블록은 Markdown 스타일로 포함하세요.
"""

    gpt_response = await query_gpt(prompt)

    # 세션 업데이트
    session_store[req.session_id] = session

    return {
        "message": gpt_response,
        "session_state": session
    }
