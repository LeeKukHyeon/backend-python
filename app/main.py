import json
import os
import gitlab
import openai
from fastapi import FastAPI
from pydantic import BaseModel
from io import StringIO
from ruamel.yaml import YAML

GITLAB_URL = os.environ.get("GITLAB_URL", "192.168.113.26:1081")
GITLAB_TOKEN = os.environ.get("GITLAB_TOKEN") # Personal Access Token (api scope 필수)
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

openai.api_key = OPENAI_API_KEY

# GitLab 클라이언트 초기화
gl = gitlab.Gitlab(GITLAB_URL, private_token=GITLAB_TOKEN)

app = FastAPI(title="GitLab CI/CD GPT Manager")

# -------------------------------
# Request 모델
# -------------------------------
class ChatRequest(BaseModel):
    user_id: str
    message: str

# -------------------------------
# 세션 상태 관리
# -------------------------------
sessions = {}

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


def get_gitlab_project(url_or_path: str):
    """URL에서 프로젝트 경로 추출 및 객체 반환"""
    # 예: https://gitlab.com/mygroup/myproject.git -> mygroup/myproject
    clean_path = url_or_path.replace(GITLAB_URL, "").replace(".git", "").strip("/")
    if clean_path.startswith("http"):  # 다른 도메인 입력 시 방어
        clean_path = clean_path.split("/")[-2] + "/" + clean_path.split("/")[-1]

    try:
        project = gl.projects.get(clean_path)
        return project
    except Exception as e:
        print(f"Error finding project: {e}")
        return None

def commit_file(project, file_path, content, commit_message, branch="main"):
    """GitLab API를 사용하여 파일 생성 또는 수정"""
    try:
        f = project.files.get(file_path=file_path, ref=branch)
        # 파일이 존재하면 업데이트
        f.content = content
        f.save(branch=branch, commit_message=commit_message, encoding='text')
        return "updated"
    except gitlab.exceptions.GitlabGetError:
        # 파일이 없으면 생성
        project.files.create({
            'file_path': file_path,
            'branch': branch,
            'content': content,
            'commit_message': commit_message
        })
        return "created"


@app.post("/api/ci/chat")
async def ci_chat(req: ChatRequest):
    # 세션 초기화
    if req.user_id not in sessions:
        sessions[req.user_id] = {"stage": "url_parse"}

        session = sessions[req.user_id]

        # ==========================================
        # 1) GitLab 프로젝트 URL 파싱
        # ==========================================
        if session["stage"] == "url_parse":
            prompt = f"""
                사용자 메시지: "{req.message}"
                이 메시지에서 GitLab 프로젝트 URL을 추출하고 URL만 반환하세요.
                URL이 없다면 빈 문자열을 반환하세요.
                """
            gpt_output = await query_gpt(prompt)
            url = gpt_output.strip()

            if "http" not in url:
                return {"message": "GitLab URL을 찾을 수 없습니다. 올바른 URL을 입력해주세요."}

            project = get_gitlab_project(url)
            if not project:
                return {"message": "GitLab 프로젝트를 찾을 수 없거나 접근 권한이 없습니다. 토큰과 URL을 확인해주세요."}

            session.update({
                "stage": "dockerfile_check",
                "project_id": project.id,
                "project_path_with_namespace": project.path_with_namespace,
                "web_url": project.web_url,
                "default_branch": project.default_branch or "main"
            })
            # Dockerfile 존재 여부 확인
            try:
                project.files.get(file_path="Dockerfile", ref=session["default_branch"])
                dockerfile_exists = True
            except:
                dockerfile_exists = False

            if not dockerfile_exists:
                        # 주 언어 감지 (간단히 가장 많이 쓰인 언어)
                        langs = project.languages()
                        primary_lang = list(langs.keys())[0] if langs else "Python"
                        session["primary_lang"] = primary_lang
                        return {"message": f"프로젝트({project.path_with_namespace}) 확인 완료.\n"
                                           f"Dockerfile이 없습니다. 주 언어인 '{primary_lang}' 기반으로 생성할까요? (예/아니오)"}
            else:
                session["stage"] = "agent_check"
                return {"message": f"프로젝트 연결 성공. Dockerfile이 이미 존재합니다.\n다음으로 GitLab Agent 연결 정보를 입력받겠습니다."}

        # ==========================================
        # 2) Dockerfile 생성
        # ==========================================
        elif session["stage"] == "dockerfile_check":
            project = gl.projects.get(session["project_id"])

            prompt = f"""
            사용자 메시지: "{req.message}"

            당신은 Dockerfile 생성 여부를 묻는 질문("주 언어(예: Python) 기반으로 생성할까요? (예/아니오)")에 대한 사용자의 응답을 분석해야 합니다.

            1. 사용자가 '네', '예', 'ok', '응' 등 긍정의 의미를 전달했다면, 'status'는 'AGREE'로 설정합니다.
            2. 사용자가 '아니오'이거나, 'Java', 'Node.js', 'Go' 등 특정 언어 이름을 제시했다면, 'status'는 'DISAGREE'로 설정하고, 'language' 필드에 사용자가 제시한 언어 이름 또는 'DISAGREE'를 그대로 넣습니다.

            JSON 형식으로만 결과를 반환하세요. 절대로 설명이나 추가 텍스트를 붙이지 마세요.

            예시 1 (긍정): {{"status": "AGREE"}}
            예시 2 (언어 제시): {{"status": "DISAGREE", "language": "Java"}}
            예시 3 (부정): {{"status": "DISAGREE", "language": "DISAGREE"}}
            """
            response_str = await query_gpt(prompt)
            intent_result = json.loads(response_str)
            if intent_result.get("status") == "AGREE":
                # 긍정 응답이면, 기존 primary_lang 그대로 사용
                target_lang = session.get("primary_lang", "Python")

            elif intent_result.get("status") == "DISAGREE":
                # 부정 또는 언어 제시 응답
                user_input_lang = intent_result.get("language")
                if user_input_lang and user_input_lang != "DISAGREE":
                    # 사용자가 특정 언어를 제시한 경우 (예: Java)
                    target_lang = user_input_lang
                else:
                    # 사용자가 단순히 부정(아니오)만 한 경우, 기본 언어 사용 또는 재질문 필요
                    # 여기서는 재질문을 위해 상태를 유지하고 메시지를 반환합니다.
                    return {"message": "Dockerfile 생성을 원하지 않으시거나 다른 언어를 원하신다면, 원하시는 언어 이름(예: Node.js)을 명확히 입력해주세요."}

            else:
                # GPT 응답 오류 시 처리
                return {"message": "응답 분석 중 오류가 발생했습니다. '예' 또는 언어 이름을 입력해주세요."}


            # GPT에게 Dockerfile 작성 요청
            prompt = f"""
            언어: {target_lang}
            프로젝트 상황: GitLab CI에서 빌드될 예정.
            최적의 Dockerfile 내용만 출력하세요. 마크다운 없이 raw text로.
            """
            dockerfile_content = await query_gpt(prompt)

            # GitLab API로 커밋
            commit_file(project, "Dockerfile", dockerfile_content, "Add Dockerfile via GPT Manager",
                        session["default_branch"])

            session["stage"] = "agent_check"
            return {
                "message": f"{target_lang} 기반 Dockerfile이 생성되었습니다.\n이제 배포를 위한 **GitLab Agent 이름**을 정해주세요.\n(예: test-agent)"}

        elif session["stage"] == "agent_check":
            # 1. GPT에게 Agent 이름만 요청
            prompt = f"""
                    사용자 메시지: "{req.message}"
                    이 메시지에서 GitLab Kubernetes Agent의 이름만 추출하세요.
                    이름 외의 다른 텍스트는 무시하고 Agent 이름만 반환하세요.
                    """
            agent_name = (await query_gpt(prompt)).strip()

            if not agent_name:
                return {"message": "Agent 이름을 추출하지 못했습니다. Agent 이름만 입력해주세요. (예: my-k8s-agent)"}

            agent_repo_path = "test1"

            try:
                agent_management_project = gl.projects.get(agent_repo_path)
            except Exception:
                return {"message": f"Agent 관리 프로젝트 경로({agent_repo_path})를 찾을 수 없거나 접근 권한이 없습니다. 관리 프로젝트 경로를 확인해주세요."}

            config_file_path = f".gitlab/agents/{agent_name}/config.yaml"
            app_project_path = session["project_path_with_namespace"]
            app_project_id = session["project_id"]

            try:
                # 파일 가져오기 (UPDATE 모드 시도)
                f = agent_management_project.files.get(file_path=config_file_path, ref=session["default_branch"])
                yaml_content = f.decode().decode('utf-8')
                yaml = YAML()
                config_data = yaml.load(yaml_content)
                config_data.setdefault('ci_access', {}).setdefault('projects', [])
                is_already_listed = any(
                    p.get('id') == app_project_id or
                    p.get('id') == app_project_path for p in config_data['ci_access']['projects']
                )
                update_message = "Agent 설정 파일에 현재 프로젝트 권한을 추가했습니다."
                if not is_already_listed:
                    config_data['ci_access']['projects'].append({'id': app_project_path})
                else:
                    update_message = "Agent 설정 파일에 현재 프로젝트 권한이 이미 존재합니다."

                # 업데이트된 YAML 내용을 StringIO로 변환
                stream = StringIO()
                yaml.dump(config_data, stream)
                new_yaml_content = stream.getvalue()

                # GitLab API를 통해 파일 업데이트 커밋
                f.content = new_yaml_content
                f.save(
                    branch=session["default_branch"],
                    commit_message=update_message,
                    encoding='text'
                )

                action_status = "수정 및 커밋"

            except gitlab.exceptions.GitlabGetError:
                # 파일이 존재하지 않음 (CREATE 모드)
                new_yaml_content = f"""
                ci_access:
                  projects:
                    - id: {app_project_path}
                """
                # GitLab API를 통해 새 파일 커밋
                agent_management_project.files.create({
                    'file_path': config_file_path,
                    'branch': session["default_branch"],
                    'content': new_yaml_content.strip(),
                    'commit_message': f"Add initial config for Agent {agent_name} and grant access to {app_project_path}"
                })

                action_status = "새로 생성 및 커밋"

            except Exception as e:
                return {"message": f"Agent 설정 파일 처리 중 예기치 않은 오류 발생: {str(e)}"}

            # 5. 세션 데이터 저장 및 다음 단계로 이동
            session["agent_name"] = agent_name
            session["agent_path"] = agent_repo_path  # 'test1' 저장

            session["stage"] = "generate_manifests"

            return {
                "message": f"""
                        ✅ Agent 관리 프로젝트 **{agent_repo_path}**에 **{agent_name}** Agent 설정 파일이 **{action_status}**되었습니다.
                        (현재 프로젝트 **{app_project_path}**에 대한 CI/CD 접근 권한 부여 완료)

                        이제 Kubernetes 배포 파일(YAML)과 .gitlab-ci.yml을 생성하고 커밋하겠습니다.
                        배포할 네임스페이스(namespace)를 입력해주세요. (기본값: default)
                        """
            }
        elif session["stage"] == "generate_manifests":
            prompt = f"""
                              사용자 메시지: "{req.message}"
                              이 메시지에서 네임스페이스의 이름만 추출하세요.
                              이름 외의 다른 텍스트는 무시하고 네임스페이스 이름만 반환하세요.
                              """
            namespace = (await query_gpt(prompt)).strip()
            if not namespace:
                namespace = "default"

            project = gl.projects.get(session["project_id"])
            app_name = project.path  # 프로젝트 이름을 앱 이름으로 사용
            # GitLab Registry 이미지 주소
            # 예: registry.gitlab.com/group/project:commit-sha
            image_placeholder = "$CI_REGISTRY_IMAGE:$CI_COMMIT_SHORT_SHA"

