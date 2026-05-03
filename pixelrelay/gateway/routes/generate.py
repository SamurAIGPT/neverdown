"""Pixelrelay-native generate endpoints — async by default, ?wait=true for sync."""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..schemas import GenerateRequest, JobResponse


def make_router(auth_dep) -> APIRouter:
    router = APIRouter(dependencies=[Depends(auth_dep)])

    @router.post("/v1/generate", response_model=JobResponse)
    async def create_generation(
        req: GenerateRequest,
        request: Request,
        wait: bool = Query(default=False),
    ) -> JobResponse:
        config = request.app.state.config
        dispatcher = request.app.state.dispatcher
        jobs = request.app.state.jobs

        providers = req.providers or list(config.default_providers)
        # Filter to providers we actually have configured
        providers = [p for p in providers if p in request.app.state.providers]
        if not providers:
            raise HTTPException(
                status_code=400,
                detail="No configured providers in request — set FAL_KEY / REPLICATE_API_TOKEN",
            )

        job = await jobs.create_job(
            model=req.model,
            prompt=req.prompt,
            extra=req.extra or {},
            webhook_url=req.webhook_url,
            providers=providers,
        )
        job = await dispatcher.submit_next_provider(job)

        if wait and job.status not in ("succeeded", "failed"):
            job = await dispatcher.wait_for_terminal(
                job.id, timeout_s=config.job_deadline_seconds
            ) or job

        return _to_response(job)

    @router.get("/v1/jobs/{job_id}", response_model=JobResponse)
    async def get_job(job_id: str, request: Request) -> JobResponse:
        job = await request.app.state.jobs.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return _to_response(job)

    @router.get("/v1/jobs", response_model=List[JobResponse])
    async def list_jobs(
        request: Request, limit: int = Query(default=50, le=500)
    ) -> List[JobResponse]:
        jobs_list = await request.app.state.jobs.list_recent(limit=limit)
        return [_to_response(j) for j in jobs_list]

    return router


def _to_response(job) -> JobResponse:
    return JobResponse(
        job_id=job.id,
        status=job.status,
        provider=job.provider,
        model=job.model,
        prompt=job.prompt,
        image_url=job.image_url,
        error=job.error,
        attempts=job.attempts or [],
        webhook_url=job.webhook_url,
        created_at=job.created_at,
        completed_at=job.completed_at,
    )
