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
    # 1) GPT에게 메시지 분석하여 GitHub owner/repo 가져오기
    parse_prompt = f"""
사용자 메시지: "{req.message}"

GitHub URL을 찾아서 owner와 repo 이름을 JSON으로 반환하세요.
출력 형식:
{{
  "owner": "...",
  "repo": "...",
  "error": null
}}
URL이 없거나 잘못되면 error에 설명을 넣으세요.
"""
    gpt_output = await query_gpt(parse_prompt)
    try:
        repo_info = json.loads(gpt_output)
        error = repo_info.get("error")
        if error:
            return {"message": f"GitHub URL 처리 오류: {error}"}
        owner, repo = repo_info.get("owner"), repo_info.get("repo")
    except Exception as e:
        return {"message": f"GPT 응답 파싱 실패: {str(e)}", "gpt_output": gpt_output}

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
