import os
import openai
import asyncio

from services.command_registry import ACTION_HANDLERS

openai.api_key = os.getenv("OPENAI_API_KEY") #k8s Secret에서 주입

async def parse_command(text: str):
    prompt = f"""
        당신은 Kubernetes 전문가입니다.
        사용자가 입력한 명령을 JSON 형태로 변환하세요.
        JSON 예시: {{ "action": "create_pod", "name": "mypod", "image": "nginx", "cpu": "1", "memory": "1Gi" }}
        입력: {text}
        """
    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[{"role": "user", "content": prompt}],
        temperature=0
    )
    content = response['choices'][0]['message']['content']
    try:
        command_json = eval(content)  # 실제 환경에서는 json.loads(content)
    except Exception as e:
        return {"status": "error", "message": f"GPT 명령 변환 실패: {str(e)}", "content": content}

    action_name = command_json.get("action")
    handler = ACTION_HANDLERS.get(action_name)
    if not handler:
        return {"status": "error", "message": f"지원하지 않는 명령: {action_name}"}

    if asyncio.iscoroutinefunction(handler):
        return await handler(command_json)
    else:
        return handler(command_json)
