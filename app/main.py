import os

import openai
from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional
import httpx
import json
from github import Github

# 환경변수 (Secret에서 로드)
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
DOCKERHUB_USERNAME = os.environ.get("DOCKERHUB_USERNAME")
DOCKERHUB_PASSWORD = os.environ.get("DOCKERHUB_PASSWORD")
ARGOCD_URL = os.environ.get("ARGOCD_URL")
ARGOCD_TOKEN = os.environ.get("ARGOCD_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

# GitHub 객체
gh = Github(GITHUB_TOKEN)

app = FastAPI(title="CI/CD GPT Manager")

# -------------------------------
# Request 모델
# -------------------------------
class ChatRequest(BaseModel):
    message: str  # 사용자가 보낸 전체 메시지

# -------------------------------
# 유틸 함수
# -------------------------------

def parse_github_url(url: str):
    """
    GitHub URL을 받아 owner와 repo 이름을 반환합니다.
    예:
        https://github.com/LeeKukHyeon/k8s-ai-manager-deploy.git
        -> owner="LeeKukHyeon", repo="k8s-ai-manager-deploy"
    """
    # URL 끝에 / 제거, .git 제거
    url = url.rstrip("/").replace(".git", "")
    parts = url.split("/")
    if len(parts) < 2:
        raise ValueError(f"올바르지 않은 GitHub URL: {url}")
    owner, repo = parts[-2], parts[-1]
    return owner, repo

async def query_gpt(prompt: str) -> str:
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7
    )
    return response.choices[0].message.content

async def check_dockerfile_exists(owner, repo):
    try:
        repository = gh.get_repo(f"{owner}/{repo}")
        repository.get_contents("Dockerfile")
        return True
    except:
        return False

async def generate_dockerfile(owner, repo, content):
    repository = gh.get_repo(f"{owner}/{repo}")
    try:
        repository.create_file("Dockerfile", "Add Dockerfile", content)
        return True
    except:
        return False

async def check_dockerhub_repo(repo_name):
    url = f"https://hub.docker.com/v2/repositories/{DOCKERHUB_USERNAME}/{repo_name}/"
    async with httpx.AsyncClient() as client:
        res = await client.get(url, auth=(DOCKERHUB_USERNAME, DOCKERHUB_PASSWORD))
        return res.status_code == 200

async def create_dockerhub_repo(repo_name, description=""):
    url = f"https://hub.docker.com/v2/repositories/"
    data = {"name": repo_name, "description": description, "is_private": False}
    async with httpx.AsyncClient() as client:
        res = await client.post(url, auth=(DOCKERHUB_USERNAME, DOCKERHUB_PASSWORD), json=data)
        return res.status_code == 201

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

# -------------------------------
# 대화형 API 엔드포인트
# -------------------------------
@app.post("/api/ci/chat")
async def ci_chat(req: ChatRequest):
    parse_prompt = f"""
    사용자 메시지: "{req.message}"

    이 메시지에서 GitHub URL 하나만 추출해서 그대로 출력하세요.
    예: https://github.com/owner/repo.git
    URL이 없으면 빈 문자열("")을 반환하세요.
    """
    gpt_output = await query_gpt(parse_prompt)
    github_url = gpt_output.strip().split()[0]  # 혹시 GPT가 부가 텍스트를 붙여도 첫 단어만 가져오기

    if not github_url.startswith("https://github.com/"):
        return {"message": "GitHub URL을 찾을 수 없습니다.", "gpt_output": gpt_output}

    # 기존 parse_github_url 함수로 owner, repo 분리
    owner, repo = parse_github_url(github_url)

    # 2) Dockerfile 확인
    dockerfile_exists = await check_dockerfile_exists(owner, repo)
    dockerhub_repo_exists = await check_dockerhub_repo(repo)

    # GPT에게 현재 상태와 대화형 진행 안내
    prompt = f"""
사용자가 repository를 배포하고 싶어합니다.
GitHub repo: {owner}/{repo}
현재 상태:
- Dockerfile 존재 여부: {dockerfile_exists}
- Docker Hub Repo 존재 여부: {dockerhub_repo_exists}

사용자 메시지: {req.message}

단계별로 대화형 안내:
1) Dockerfile 생성 필요 시 예제 코드 제공
2) GitHub Action workflow 안내
3) Docker Hub 이미지 푸시 안내
4) ArgoCD Application 생성 안내
"""
    gpt_response = await query_gpt(prompt)

    # Dockerfile 없는 경우 GPT 안내 내용 기반으로 생성
    if not dockerfile_exists:
        # 예제 Dockerfile 요청
        dockerfile_prompt = f"GitHub repo {owner}/{repo}에 적합한 Dockerfile 예제를 만들어주세요."
        dockerfile_content = await query_gpt(dockerfile_prompt)
        success = await generate_dockerfile(owner, repo, dockerfile_content)
        if success:
            gpt_response += "\n\nDockerfile을 생성하고 GitHub에 푸시했습니다."
            dockerfile_exists = True
        else:
            gpt_response += "\n\nDockerfile 생성 중 오류 발생!"

    # Docker Hub 레포 없으면 생성
    if not dockerhub_repo_exists:
        dockerhub_prompt = f"사용자가 Docker Hub에 {repo} 이름으로 레포를 만들고 싶어합니다. 생성해도 될까요?"
        create_resp = await query_gpt(dockerhub_prompt)
        # 간단히 yes 포함 여부로 판단
        if "yes" in create_resp.lower():
            success = await create_dockerhub_repo(repo)
            if success:
                gpt_response += f"\n\nDocker Hub 레포 {repo}를 생성했습니다."
                dockerhub_repo_exists = True
            else:
                gpt_response += "\n\nDocker Hub 레포 생성 실패!"

    return {
        "message": gpt_response,
        "dockerfile_exists": dockerfile_exists,
        "dockerhub_repo_exists": dockerhub_repo_exists
    }
