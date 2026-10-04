"""Step 0 placeholder. Full routes arrive in build step 9."""

from fastapi import FastAPI

app = FastAPI(title="DB Normalization Checker")


@app.get("/api/health")
def health():
    return {"ok": True}
