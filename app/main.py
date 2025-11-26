import os
import openai
from fastapi import FastAPI
from pydantic import BaseModel
from github import Github

import httpx

import subprocess

# 환경변수
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
DOCKERHUB_USERNAME = os.environ.get("DOCKERHUB_USERNAME")
DOCKERHUB_PASSWORD = os.environ.get("DOCKERHUB_PASSWORD")
ARGOCD_URL = os.environ.get("ARGOCD_URL")
ARGOCD_TOKEN = os.environ.get("ARGOCD_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

gh = Github(GITHUB_TOKEN)

app = FastAPI(title="CI/CD GPT Manager")

# -------------------------------
# Request 모델
# -------------------------------
class ChatRequest(BaseModel):
    user_id: str
    message: str  # 사용자 메시지

# -------------------------------
# 세션 상태 관리 (메모리 예시)
# -------------------------------
sessions = {}

# -------------------------------
# 유틸 함수
# -------------------------------
async def query_gpt(prompt: str) -> str:
    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3
    )
    return response.choices[0].message.content

def parse_github_url(url: str):
    url = url.rstrip("/").replace(".git", "")
    parts = url.split("/")
    return parts[-2], parts[-1]

async def check_dockerfile_exists(owner, repo):
    try:
        repository = gh.get_repo(f"{owner}/{repo}")
        repository.get_contents("Dockerfile")
        return True
    except:
        return False

async def generate_dockerfile(owner, repo, content):
    repo_obj = gh.get_repo(f"{owner}/{repo}")
    try:
        repo_obj.create_file("Dockerfile", "Add Dockerfile", content)
        return True
    except:
        return False

async def check_dockerhub_repo(repo_name):
    url = f"https://hub.docker.com/v2/repositories/{DOCKERHUB_USERNAME}/{repo_name}/"
    async with httpx.AsyncClient() as client:
        res = await client.get(url, auth=(DOCKERHUB_USERNAME, DOCKERHUB_PASSWORD))
        return res.status_code == 200

async def create_dockerhub_repo(repo_name: str):
    url = "https://hub.docker.com/v2/repositories/"
    data = {
        "namespace": DOCKERHUB_USERNAME,
        "name": repo_name,
        "is_private": False
    }

    async with httpx.AsyncClient() as client:
        res = await client.post(
            url,
            json=data,
            auth=(DOCKERHUB_USERNAME, DOCKERHUB_PASSWORD)
        )
        return res.status_code in [200, 201]


async def dockerhub_repo_exists(repo_name: str):
    url = f"https://hub.docker.com/v2/repositories/{DOCKERHUB_USERNAME}/{repo_name}/"

    async with httpx.AsyncClient() as client:
        res = await client.get(url)
        return res.status_code == 200

# -------------------------------
# 대화형 API
# -------------------------------
@app.post("/api/ci/chat")
async def ci_chat(req: ChatRequest):
    # 세션 초기화
    if req.user_id not in sessions:
        sessions[req.user_id] = {"stage": "url_parse", "github_url": None, "owner": None, "repo": None}

    session = sessions[req.user_id]

    # -----------------------
    # 1) URL 추출 단계
    # -----------------------
    if session["stage"] == "url_parse":
        prompt = f"""
사용자 메시지: "{req.message}"

이 메시지에서 GitHub URL 하나만 추출해서 그대로 출력하세요.
예: https://github.com/owner/repo.git
URL이 없으면 빈 문자열("")을 반환하세요.
"""
        gpt_output = await query_gpt(prompt)
        github_url = gpt_output.strip().split()[0]

        if not github_url.startswith("https://github.com/"):
            return {"message": "GitHub URL을 찾을 수 없습니다.", "gpt_output": gpt_output}

        owner, repo = parse_github_url(github_url)
        session.update({
            "stage": "dockerfile_check",
            "github_url": github_url,
            "owner": owner,
            "repo": repo
        })

        owner, repo = session["owner"], session["repo"]
        dockerfile_exists = await check_dockerfile_exists(owner, repo)
        session["dockerfile_exists"] = dockerfile_exists

        if not dockerfile_exists:
            repo_obj = gh.get_repo(f"{owner}/{repo}")
            languages = repo_obj.get_languages()
            primary_lang = max(languages, key=languages.get)
            session["primary_lang"] = primary_lang
            return {"message": f"Dockerfile이 없습니다. 생성합니다. 주언어가 {primary_lang}이 맞나요?"}
        else:
            session["stage"] = "dockerhub_check"
            return {"message": "Dockerfile이 이미 존재합니다. 다음 단계: Docker Hub 확인."}
    # -----------------------
    # 2) Dockerfile 확인 단계
    # -----------------------
    elif session["stage"] == "dockerfile_check":
        primary_lang = session["primary_lang"]
        github_url = session["github_url"]
        owner = session["owner"]
        repo = session["repo"]

        repo_path = f"/tmp/{owner}_{repo}"
        dockerfile_path = os.path.join(repo_path, "Dockerfile")

        lang_check_prompt = f"""
            사용자가 말한 GitHub repo의 추정 주 언어는 {primary_lang}입니다.
            최적의 Dockerfile을 생성해주세요.
            """

        dockerfile_content = await query_gpt(lang_check_prompt)

        with open(dockerfile_path, "w", encoding="utf-8") as f:
            f.write(dockerfile_content.strip())

        push_url = github_url.replace(
            "https://", f"https://{GITHUB_TOKEN}@"
        )

        subprocess.run(["git", "-C", repo_path, "add", "Dockerfile"], check=True)
        subprocess.run(["git", "-C", repo_path, "commit", "-m", "Add auto-generated Dockerfile"], check=True)
        subprocess.run(["git", "-C", repo_path, "push", push_url, "HEAD"], check=True)

        session["stage"] = "dockerhub_check"
        return {
            "message": f" {primary_lang} 기준으로 Dockerfile을 생성 성공입니다. docker hub 레포지토리는 어떤 이름을 사용하시겠습니까?"
        }


    # 4) Docker Hub 확인 단계
    # -----------------------
    elif session["stage"] == "dockerhub_check":
        repo_name = req.message.strip()
        session["dockerhub_repo_name"] = repo_name
        exists = await dockerhub_repo_exists(repo_name)
        if exists:
            return {
                "message": f"도커허브 레포지토리 '{repo_name}'가 존재합니다. 다음 단계로 넘어갑니다."
            }
        created = await create_dockerhub_repo(repo_name)


    # -----------------------
    # 5) Docker Hub 생성 단계
    # -----------------------
    elif session["stage"] == "dockerhub_create":
        if "예" in req.message:
            repo = session["repo"]
            success = await create_dockerhub_repo(repo)
            session["dockerhub_repo_exists"] = success
            session["stage"] = "workflow_create"
            return {"message": f"Docker Hub 레포 {repo} 생성 완료. 다음 단계: GitHub Action workflow 생성."}
        else:
            session["stage"] = "workflow_create"
            return {"message": "Docker Hub 생성 건너뜀. 다음 단계: GitHub Action workflow 생성."}

    # -----------------------
    # 6) GitHub Action / ArgoCD 단계
    # -----------------------
    elif session["stage"] == "workflow_create":
        session["stage"] = "completed"
        return {"message": "여기서 GitHub Action workflow 생성, ArgoCD Application 생성, CI/CD 자동 배포를 진행하도록 합니다."}

    # -----------------------
    # 완료
    # -----------------------
    elif session["stage"] == "completed":
        return {"message": "배포 프로세스가 이미 완료되었습니다."}

    return {"message": "알 수 없는 상태입니다."}
