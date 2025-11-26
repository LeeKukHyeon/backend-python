import os
from fastapi import FastAPI
from pydantic import BaseModel
import httpx
from fastapi.middleware.cors import CORSMiddleware
import openai

app = FastAPI(title="K8s AI Manager GPT Chat")

# CORS 허용 (React에서 접근 가능)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 🔑 시크릿 환경변수
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DOCKERHUB_USERNAME = os.getenv("DOCKERHUB_USERNAME")
DOCKERHUB_PASSWORD = os.getenv("DOCKERHUB_PASSWORD")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

openai.api_key = OPENAI_API_KEY

class ChatRequest(BaseModel):
    github_url: str
    message: str  # 사용자가 입력한 자연어 명령
    session_id: str = None  # 대화 상태 유지용(선택)

def parse_github_url(url: str):
    url = url.replace("https://github.com/", "")
    parts = url.split("/")
    return parts[0], parts[1]

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
    # OpenAI GPT API 호출 (ChatCompletion)
    response = openai.ChatCompletion.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3
    )
    return response.choices[0].message.content

@app.post("/api/ci/chat")
async def ci_chat(req: ChatRequest):
    owner, repo = parse_github_url(req.github_url)
    dockerfile_exists = await check_dockerfile_exists(owner, repo)
    dockerhub_repo_exists = await check_dockerhub_repo(repo)

    # GPT 프롬프트 구성 (대화형 시나리오)
    prompt = f"""
사용자가 깃허브 URL을 전달했습니다: {req.github_url}
현재 상태:
- Dockerfile 존재 여부: {dockerfile_exists}
- Docker Hub Repo 존재 여부: {dockerhub_repo_exists}

사용자 명령: {req.message}

대화형으로 안내하면서 다음 단계별로 진행하세요:
1) Dockerfile 생성 여부 질문
2) Dockerfile 내용 생성 및 예제 제시
3) GitHub Action workflow 생성 안내
4) Docker Hub 이미지 푸시 안내
대화형 메시지와 필요한 코드 블록을 함께 반환하세요.
"""

    gpt_response = await query_gpt(prompt)
    return {"message": gpt_response, "dockerfile_exists": dockerfile_exists, "dockerhub_repo_exists": dockerhub_repo_exists}
