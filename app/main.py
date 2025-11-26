import os

import openai
from fastapi import FastAPI
from pydantic import BaseModel
from github import Github
import httpx
from typing import Optional
import re
import asyncio

# 환경변수
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
DOCKERHUB_USERNAME = os.environ.get("DOCKERHUB_USERNAME")
DOCKERHUB_PASSWORD = os.environ.get("DOCKERHUB_PASSWORD")
ARGOCD_URL = os.environ.get("ARGOCD_URL")
ARGOCD_TOKEN = os.environ.get("ARGOCD_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

gh = Github(GITHUB_TOKEN)
app = FastAPI(title="GPT 기반 대화형 CI/CD Manager")

# -------------------------------
# Request 모델
# -------------------------------
class ChatRequest(BaseModel):
    message: str
    github_url: Optional[str] = None

# -------------------------------
# 상태 관리 (메모리 기반, 필요시 Redis 등으로 변경 가능)
# -------------------------------
sessions = {}  # {github_url: {"stage": str, "dockerfile_exists": bool, "dockerhub_exists": bool}}

# -------------------------------
# 유틸 함수
# -------------------------------
def parse_github_url(url: str):
    parts = url.rstrip("/").split("/")
    return parts[-2], parts[-1]

async def check_dockerfile_exists(owner, repo):
    try:
        gh.get_repo(f"{owner}/{repo}").get_contents("Dockerfile")
        return True
    except:
        return False

async def check_dockerhub_repo(repo_name):
    url = f"https://hub.docker.com/v2/repositories/{DOCKERHUB_USERNAME}/{repo_name}/"
    async with httpx.AsyncClient() as client:
        res = await client.get(url, auth=(DOCKERHUB_USERNAME, DOCKERHUB_PASSWORD))
        return res.status_code == 200

async def generate_dockerfile(owner, repo):
    dockerfile_content = """FROM python:3.11-slim
WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir fastapi uvicorn httpx PyGithub
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
"""
    repository = gh.get_repo(f"{owner}/{repo}")
    try:
        repository.create_file("Dockerfile", "Add Dockerfile", dockerfile_content)
        return True
    except:
        return False

async def create_github_action(owner, repo, dockerhub_repo):
    workflow_content = f"""name: CI/CD

on:
  push:
    branches: [ main ]

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Build Docker image
        run: docker build -t {DOCKERHUB_USERNAME}/{dockerhub_repo}:latest .
      - name: Login DockerHub
        run: echo ${{{{ secrets.DOCKERHUB_PASSWORD }}}} | docker login -u {DOCKERHUB_USERNAME} --password-stdin
      - name: Push Docker image
        run: docker push {DOCKERHUB_USERNAME}/{dockerhub_repo}:latest
"""
    repository = gh.get_repo(f"{owner}/{repo}")
    try:
        repository.create_file(".github/workflows/ci-cd.yml", "Add CI/CD workflow", workflow_content)
        return True
    except:
        return False

async def create_argocd_app(app_name, repo_url, path, cluster_url, namespace):
    url = f"{cluster_url}/api/v1/applications"
    headers = {"Authorization": f"Bearer {ARGOCD_TOKEN}"}
    data = {
        "metadata": {"name": app_name},
        "spec": {
            "source": {"repoURL": repo_url, "path": path},
            "destination": {"server": cluster_url, "namespace": namespace},
            "project": "default",
        }
    }
    async with httpx.AsyncClient(verify=False) as client:
        res = await client.post(url, headers=headers, json=data)
        return res.status_code, res.json()

async def query_gpt(prompt: str) -> str:
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7
    )
    return response.choices[0].message.content

# -------------------------------
# 대화형 API
# -------------------------------
@app.post("/api/ci/chat")
async def ci_chat(req: ChatRequest):
    # GitHub URL 추출
    if not req.github_url:
        match = re.search(r"https://github\.com/\S+", req.message)
        if match:
            req.github_url = match.group(0)
        else:
            return {"message": "GitHub URL을 찾을 수 없습니다."}

    owner, repo = parse_github_url(req.github_url)

    # 세션 초기화
    if req.github_url not in sessions:
        dockerfile_exists = await check_dockerfile_exists(owner, repo)
        dockerhub_exists = await check_dockerhub_repo(repo)
        sessions[req.github_url] = {
            "stage": "dockerfile_check",
            "dockerfile_exists": dockerfile_exists,
            "dockerhub_exists": dockerhub_exists
        }

    session = sessions[req.github_url]

    # -------------------------------
    # 단계별 처리
    # -------------------------------
    gpt_prompt = f"""
사용자가 GitHub URL과 메시지를 보냈습니다: {req.github_url}
현재 상태: {session}
사용자 메시지: {req.message}
대화형으로 다음 단계 안내 후 필요한 코드/명령 제공
"""

    gpt_response = await query_gpt(gpt_prompt)

    # Dockerfile 생성 단계
    if session["stage"] == "dockerfile_check" and not session["dockerfile_exists"]:
        success = await generate_dockerfile(owner, repo)
        if success:
            session["dockerfile_exists"] = True
            session["stage"] = "dockerhub_check"
            gpt_response += "\n\nDockerfile 생성 완료! 다음 단계: Docker Hub 레포 확인/생성"
        else:
            gpt_response += "\n\nDockerfile 생성 실패!"

    # Docker Hub 레포 단계
    elif session["stage"] == "dockerhub_check":
        if not session["dockerhub_exists"]:
            # 여기서 GPT가 레포 이름 물어보고, 생성하도록 안내할 수 있음
            # 예시로 repo 이름 그대로 사용
            session["dockerhub_exists"] = True
            session["stage"] = "github_action"
            gpt_response += "\n\nDocker Hub 레포 생성 완료! 다음 단계: GitHub Action workflow 생성"

    # GitHub Action workflow 단계
    elif session["stage"] == "github_action":
        await create_github_action(owner, repo, repo)
        session["stage"] = "argocd_app"
        gpt_response += "\n\nGitHub Action workflow 생성 완료! 다음 단계: ArgoCD Application 생성"

    # ArgoCD 단계
    elif session["stage"] == "argocd_app":
        await create_argocd_app(repo, req.github_url, ".", ARGOCD_URL, "default")
        session["stage"] = "completed"
        gpt_response += "\n\nArgoCD Application 생성 완료! CI/CD 자동 배포 구성 완료!"

    return {
        "message": gpt_response,
        "session": session
    }
