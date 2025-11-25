from fastapi import FastAPI

from app.routers import natural_router


app = FastAPI(title="K8s AI Manager")

app.include_router(natural_router, prefix="/natural")