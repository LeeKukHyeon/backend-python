from kubernetes import client, config

def deploy_to_k8s(command_info: str) -> str:
    """
    Kubernetes 배포 수행
    """
    # 클러스터 내에서 실행될 경우 in-cluster config 사용
    try:
        config.load_incluster_config()
    except:
        # 로컬 테스트 시
        config.load_kube_config()

    api = client.AppsV1Api()

    # 예시: deployment 이름과 namespace 고정
    deployment_name = "k8s-ai-manager"
    namespace = "default"

    # TODO: command_info 기반으로 deployment spec 생성
    # 현재는 예시 메시지
    return f"Kubernetes deployment '{deployment_name}' applied in namespace '{namespace}'"
