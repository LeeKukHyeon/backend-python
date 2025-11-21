FROM python:3.11-slim

# 시스템 필수 패키지
RUN apt-get update && apt-get install -y \
    build-essential curl vim git && \
    rm -rf /var/lib/apt/lists/*

# 작업 디렉토리
WORKDIR /app

# python 패키지 설치용
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# 소스 복사
COPY . /app

# FastAPI 실행
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
