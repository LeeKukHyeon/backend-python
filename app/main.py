import json
import os
import openai
from fastapi import FastAPI
from pydantic import BaseModel
from github import Github
from github import InputGitTreeElement

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
    url = f"https://hub.docker.com/v2/repositories/{DOCKERHUB_USERNAME}/{repo_name}/"
    data = {
        "namespace": DOCKERHUB_USERNAME,
        "name": repo_name,
        "is_private": False
    }

    async with httpx.AsyncClient() as client:
        res = await client.post(
            url,
            json=data,
            auth=(DOCKERHUB_USERNAME, DOCKERHUB_PASSWORD)  # 또는 Access Token
        )
        if res.status_code in [200, 201]:
            return True
        else:
            print("Docker Hub 생성 실패:", res.status_code, res.text)
            return False


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
            return {"message": f"Dockerfile이 없습니다. 생성합니다. 주언어가 {primary_lang}로 생성하도록 하겠습니다. 그대로 진행하시려면 예 아니면 다른 언어를 입력해주세요"}
        else:
            session["stage"] = "github_actions_setup"
            return {"message": "Dockerfile이 이미 존재합니다. 다음 단계: github_actions_setup."}
    # -----------------------
    # 2) Dockerfile 확인 단계
    # -----------------------
    elif session["stage"] == "dockerfile_check":
        primary_lang = session["primary_lang"]
        message = req.message
        if "예" in message or "ok" in message.lower():
            # 기존 primary_lang 그대로 사용
            primary_lang = session["primary_lang"]
        else:
            # 사용자가 입력한 언어로 변경
            primary_lang = message.strip()
        session["primary_lang"] = primary_lang
        github_url = session["github_url"]
        owner = session["owner"]
        repo = session["repo"]

        repo_path = f"/tmp/{owner}_{repo}"
        dockerfile_path = os.path.join(repo_path, "Dockerfile")

        if not os.path.exists(repo_path):
            subprocess.run(["git", "clone", github_url, repo_path], check=True)

        lang_check_prompt = f"""
            GitHub repo의 추정 주 언어는 {primary_lang}입니다.
            최적의 Dockerfile을 생성해주세요.
            """

        dockerfile_content = await query_gpt(lang_check_prompt)
        os.makedirs(os.path.dirname(dockerfile_path), exist_ok=True)
        with open(dockerfile_path, "w", encoding="utf-8") as f:
            f.write(dockerfile_content.strip())
        subprocess.run(["git", "-C", repo_path, "config", "user.name", "Lee Kuk Hyeon"], check=True)
        subprocess.run(["git", "-C", repo_path, "config", "user.email", "0504lkh@naver.com"], check=True)

        subprocess.run(["git", "-C", repo_path, "add", "Dockerfile"], check=True)
        status = subprocess.run(
            ["git", "-C", repo_path, "status", "--porcelain"],
            capture_output=True, text=True
        )
        if status.stdout.strip():
            subprocess.run(
                ["git", "-C", repo_path, "commit", "-m", "Add auto-generated Dockerfile"],
                check=True
            )
        else:
            print("변경 사항이 없어 commit 생략")
        push_url = github_url.replace(
            "https://", f"https://{GITHUB_TOKEN}@"
        )

        subprocess.run(["git", "-C", repo_path, "push", push_url, "HEAD"], check=True)
        session["stage"] = "github_actions_setup"
        return {
            "message": f" {primary_lang} 기준으로 Dockerfile을 생성 성공입니다. 깃헙액션?"
        }


    # 4) Docker Hub 확인 단계
    # -----------------------
    elif session["stage"] == "dockerhub_check":
        repo_name = req.message.strip()
        session["dockerhub_repo_name"] = repo_name
        exists = await dockerhub_repo_exists(repo_name)
        if exists:
            session["stage"] = "github_actions_setup"
            return {
                "message": f"도커허브 레포지토리 '{repo_name}'가 존재합니다. GitHub Actions workflow를 생성합니다. 특별히 원하는 브랜치가 있나요? (예: main 브랜치 push 시 자동 빌드)"
            }
        created = await create_dockerhub_repo(repo_name)
        session["stage"] = "github_actions_setup"
        return {
            "message": f"도커허브 레포지토리 '{repo_name}'가 존재하지 않아 새로 생성했습니다! GitHub Actions workflow를 생성합니다. 특별히 원하는 브랜치가 있나요? (예: main 브랜치 push 시 자동 빌드)"
        }


    # -----------------------
    # 5) Docker Hub 생성 단계
    # -----------------------
    elif session["stage"] == "github_actions_setup":
        owner = session["owner"]
        repo = session["repo"]
        prompt = f"""
        사용자 메시지: "{req.message}"

        다음 형식으로 **정확히 JSON만** 반환하세요.  
        절대로 추가 설명을 붙이지 마세요.
        예시:
        {{
          "branch": "main",
          "os": "ubuntu-latest",
          "error": null
        }}
        """
        branch_info = await query_gpt(prompt)
        try:
            branch_data = json.loads(branch_info)
        except json.JSONDecodeError:
            branch_data = {"branch": "main", "os": "ubuntu-latest", "error": "JSONDecodeError"}

        branch = branch_data.get("branch", "main")
        os_runner = branch_data.get("os", "ubuntu-latest")
        session["branch"] = branch

        os_runner = branch_data.get("os", "ubuntu-latest")

        workflow_content = f"""
        name: Docker Build & Push

        on:
          push:
            branches: [ {branch} ]

        jobs:
          build-and-push:
            runs-on: {os_runner}
            steps:
              - uses: actions/checkout@v3
              - name: Set up Docker Buildx
                uses: docker/setup-buildx-action@v2
              - name: Log in to Docker Hub
                uses: docker/login-action@v2
                with:
                  username: ${{{{ secrets.DOCKERHUB_USERNAME }}}}
                  password: ${{{{ secrets.DOCKER_PASSWORD }}}}
              - name: Build and push Docker image
                uses: docker/build-push-action@v5
                with:
                  push: true
                  tags: {DOCKERHUB_USERNAME}/docker:${{{{ github.sha }}}}
        """
        repository = gh.get_repo(f"{owner}/{repo}")
        path = ".github/workflows/docker-build.yml"
        try:
            existing_file = repository.get_contents(path)
            repository.update_file(path, "Update Docker build workflow", workflow_content, existing_file.sha)
            session["stage"] = "argocd_setup"
            return {"message": "여기서 GitHub Action workflow 업데이트, ArgoCD Application 생성, CI/CD 자동 배포를 진행하도록 합니다. "}

        except:
            repository.create_file(path, "Add Docker build workflow", workflow_content)
            session["stage"] = "argocd_setup"
            return {"message": "여기서 GitHub Action workflow 생성, ArgoCD Application 생성, CI/CD 자동 배포를 진행하도록 합니다. namespace와 application 명을 입력해주세요"}


    elif session["stage"] == "argocd_setup":
        # --- 1. ArgoCD namespace / app_name 추출 (GPT 사용) ---
        gpt_ns_prompt = f"""
        사용자 메시지: "{req.message}"

        이 메시지에서 ArgoCD Application 생성에 필요한 namespace와 application 이름을 JSON으로 반환하세요.
        출력 형식:
        {{
          "namespace": "...",
          "app_name": "..."
        }}
        없으면 기본값 namespace='default', app_name='{session['repo']}-app'로 설정하세요.
        """
        gpt_ns_output = await query_gpt(gpt_ns_prompt)
        try:
            ns_info = json.loads(gpt_ns_output)
            namespace = ns_info.get("namespace", "default")
            app_name = ns_info.get("app_name", f"{session['repo']}-app")
        except Exception:
            namespace = "default"
            app_name = f"{session['repo']}-app"

        session.update({"namespace": namespace, "app_name": app_name})

        # --- 2. GPT에게 Deployment, Service, Kustomize, App YAML 생성 요청 ---
        gpt_yaml_prompt = f"""
        GitHub repo: {session['github_url']}
        Docker 이미지: {DOCKERHUB_USERNAME}/docker:latest
        Namespace: {namespace}
        App name: {app_name}

        이 정보를 기반으로 Kubernetes Deployment.yaml, Service.yaml, kustomization.yaml, ArgoCD Application(app.yaml) 생성해주세요.
        출력 형식은 JSON으로 해주시고 딱 json값만 응답해주세요 안그러면 에러나요:
        {{
          "deployment_yaml": "...",
          "service_yaml": "...",
          "kustomization_yaml": "...",
          "app_yaml": "..."
        }}
        """
        yaml_output = await query_gpt(gpt_yaml_prompt)
        try:
            yamls = json.loads(yaml_output)
            deployment_yaml = yamls["deployment_yaml"]
            service_yaml = yamls["service_yaml"]
            kustomization_yaml = yamls["kustomization_yaml"]
            app_yaml = yamls["app_yaml"]
        except Exception as e:
            return {"message": f"YAML 생성 실패: {str(e)}", "gpt_output": yaml_output}

        # --- 3. GitHub ArgoCD 레포지토리에 Push ---
        user = gh.get_user()
        argo_repo_name = session["repo"] + "-argoCD"
        try:
            argo_repo = user.get_repo(argo_repo_name)
        except:
            argo_repo = user.create_repo(
                name=argo_repo_name,
                description=f"{session['repo']} ArgoCD deployment repo",
                private=False
            )

        # Push 파일
        tree_elements = [
            InputGitTreeElement("deployment.yaml", "100644", "blob", deployment_yaml),
            InputGitTreeElement("service.yaml", "100644", "blob", service_yaml),
            InputGitTreeElement("kustomization.yaml", "100644", "blob", kustomization_yaml),
            InputGitTreeElement("app.yaml", "100644", "blob", app_yaml)
        ]
        base_branch = session.get("branch", "main")
        base_commit = argo_repo.get_branch(base_branch).commit.sha
        master_tree = argo_repo.get_git_tree(base_commit)
        new_tree = argo_repo.create_git_tree(tree_elements, base_tree=master_tree)
        parent = argo_repo.get_git_commit(base_commit)
        commit = argo_repo.create_git_commit("Add ArgoCD deployment files via GPT", new_tree, [parent])
        ref = argo_repo.get_git_ref(f"heads/{base_branch}")
        ref.edit(commit.sha)

        # --- 4. ArgoCD Application 등록 ---
        # app.yaml 내용은 GPT가 만들어준 것을 그대로 사용하거나, 직접 JSON 변환 후 API 호출 가능
        async with httpx.AsyncClient(verify=False) as client:
            res = await client.post(
                f"{ARGOCD_URL}/api/v1/applications",
                headers={"Authorization": f"Bearer {ARGOCD_TOKEN}"},
                json=json.loads(app_yaml)  # GPT가 반환한 app.yaml을 JSON으로 변환
            )

        session["stage"] = "done"
        return {
            "message": f"ArgoCD Application {app_name} 생성 완료. CI/CD 파이프라인이 구축되었습니다. Image Updater로 자동 배포됩니다.",
            "argocd_response": res.json()
        }
    # -----------------------
    # 완료
    # -----------------------
    elif session["stage"] == "completed":
        return {"message": "배포 프로세스가 이미 완료되었습니다."}

    return {"message": "알 수 없는 상태입니다."}
