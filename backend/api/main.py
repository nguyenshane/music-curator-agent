from fastapi import FastAPI

from backend.api.routes import auth_tidal, health, jobs, playlists, recommendations

app = FastAPI(title="Shane Music Curator Agent", version="0.1.0")
app.include_router(health.router)
app.include_router(jobs.router, prefix="/jobs", tags=["jobs"])
app.include_router(recommendations.router, prefix="/recommendations", tags=["recommendations"])
app.include_router(playlists.router)
app.include_router(auth_tidal.router)
