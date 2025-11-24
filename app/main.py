from fastapi import FastAPI, HTTPException

app = FastAPI(title="K8s AI Manager")

@app.post("/natural/command", response_model=NaturalCommandResponse)
async def natural_command(req: NaturalCommandRequest):
    """
     자연어 명령을 받아 GPT로 변환 후, CI/CD 및 쿠버네티스 배포 수행
     """
    try:
        # 1. GPT API로 명령 해석
        command_info = get_deployment_commands(req.text)

        # 2. GitHub & ArgoCD 연동 (CI/CD 구성)
        ci_cd_result = setup_ci_cd(command_info)

        # 3. Kubernetes 배포
        deploy_result = deploy_to_k8s(command_info)

        return NaturalCommandResponse(
            gpt_output=command_info,
            ci_cd_status=ci_cd_result,
            k8s_status=deploy_result
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))