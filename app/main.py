import os
from fastapi import FastAPI
from pydantic import BaseModel
import httpx
import base64
from github import Github
from typing import Optional

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
    message: str  # 사용자가 보낸 전체 메시지 (예: "이 repo 배포해줘: https://github.com/user/repo")
    github_url: Optional[str] = None

# -------------------------------
# 유틸 함수
# -------------------------------
def parse_github_url(url: str):
    # https://github.com/user/repo → owner=user, repo=repo
    parts = url.rstrip("/").split("/")
    return parts[-2], parts[-1]

async def check_dockerfile_exists(owner, repo):
    try:
        repository = gh.get_repo(f"{owner}/{repo}")
        repository.get_contents("Dockerfile")
        return True
    except:
        return False

async def check_dockerhub_repo(repo_name):
    url = f"https://hub.docker.com/v2/repositories/{DOCKERHUB_USERNAME}/{repo_name}/"
    async with httpx.AsyncClient() as client:
        res = await client.get(url, auth=(DOCKERHUB_USERNAME, DOCKERHUB_PASSWORD))
        return res.status_code == 200

async def generate_dockerfile(owner, repo):
    # GPT에게 Dockerfile 예제 요청
    # 여기서는 예시로 단순 Python FastAPI Dockerfile
    dockerfile_content = """FROM python:3.11-slim
WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir fastapi uvicorn
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
"""
    # GitHub에 Dockerfile 생성
    repository = gh.get_repo(f"{owner}/{repo}")
    try:
        repository.create_file("Dockerfile", "Add Dockerfile", dockerfile_content)
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

async def query_gpt(prompt: str):
    import openai
    openai.api_key = OPENAI_API_KEY
    resp = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[{"role":"user","content":prompt}],
        temperature=0.7
    )
    return resp.choices[0].message.content

# -------------------------------
# 대화형 API 엔드포인트
# -------------------------------
@app.post("/api/ci/chat")
async def ci_chat(req: ChatRequest):
    if not req.github_url:
        # 메시지에서 URL 추출
        import re
        match = re.search(r"https://github\.com/\S+", req.message)
        if match:
            req.github_url = match.group(0)
        else:
            return {"message": "GitHub URL을 찾을 수 없습니다."}

    owner, repo = parse_github_url(req.github_url)

    dockerfile_exists = await check_dockerfile_exists(owner, repo)
    dockerhub_repo_exists = await check_dockerhub_repo(repo)

    prompt = f"""
사용자가 깃허브 URL을 전달했습니다: {req.github_url}
현재 상태:
- Dockerfile 존재 여부: {dockerfile_exists}
- Docker Hub Repo 존재 여부: {dockerhub_repo_exists}

사용자 메시지: {req.message}

단계별 대화식으로 안내하며 다음 작업 진행:
1) Dockerfile 생성 필요 시 예제 코드 제공
2) GitHub Action workflow 안내
3) Docker Hub 이미지 푸시 안내
4) ArgoCD Application 생성 안내
"""

    gpt_response = await query_gpt(prompt)

    # Dockerfile 없는 경우 자동 생성
    if not dockerfile_exists:
        success = await generate_dockerfile(owner, repo)
        if success:
            gpt_response += "\n\nDockerfile을 생성하고 GitHub에 푸시했습니다."
            dockerfile_exists = True
        else:
            gpt_response += "\n\nDockerfile 생성 중 오류 발생!"

    return {
        "message": gpt_response,
        "dockerfile_exists": dockerfile_exists,
        "dockerhub_repo_exists": dockerhub_repo_exists
    }
