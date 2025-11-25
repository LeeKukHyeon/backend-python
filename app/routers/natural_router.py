from fastapi import APIRouter
from app.models.analyze_models import AnalyzeRequest
from app.services.analyzer_service import analyze_text

router = APIRouter()

@router.post("/analyze")
async def analyze(request: AnalyzeRequest):
    result = await analyze_text(request.text, request.github_url)
    # 분류 결과에 따라 후처리 실행
    if result.action_type == "cicd":

        return {"analysis": result}

    elif result.action_type == "k8s_api":

        return {"analysis": result}

    return {"analysis": result, "message": "요청을 이해하지 못했습니다."}