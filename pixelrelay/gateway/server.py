"""FastAPI app factory + lifespan management."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Dict, Optional

from fastapi import FastAPI

from ..providers.base import BaseProvider
from ..providers.fal import FalProvider
from ..providers.google import GoogleProvider
from ..providers.openai import OpenAIProvider
from ..providers.replicate import ReplicateProvider
from .auth import make_auth_dependency
from .config import GatewayConfig
from .db import create_all, make_engine, make_session_factory
from .dispatcher import Dispatcher
from .routes.callbacks import router as callbacks_router
from .routes.generate import make_router as make_generate_router
from .routes.health import router as health_router
from .stores.sql import SqlCooldownStore, SqlJobStore
from .worker import failover_loop

logger = logging.getLogger(__name__)


def _build_provider_registry(config: GatewayConfig) -> Dict[str, BaseProvider]:
    registry: Dict[str, BaseProvider] = {}
    if config.fal_key:
        registry["fal"] = FalProvider(api_key=config.fal_key)
    if config.replicate_token:
        registry["replicate"] = ReplicateProvider(api_key=config.replicate_token)
    if config.openai_key:
        registry["openai"] = OpenAIProvider(api_key=config.openai_key)
    if config.google_key:
        registry["google"] = GoogleProvider(api_key=config.google_key)
    return registry


def create_app(config: Optional[GatewayConfig] = None) -> FastAPI:
    config = config or GatewayConfig.from_env()
    config.validate()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = make_engine(config.database_url)
        session_factory = make_session_factory(engine)
        await create_all(engine)

        jobs_store = SqlJobStore(session_factory)
        cooldowns_store = SqlCooldownStore(session_factory)
        providers = _build_provider_registry(config)

        dispatcher = Dispatcher(
            config=config,
            providers=providers,
            jobs=jobs_store,
            cooldowns=cooldowns_store,
        )

        app.state.config = config
        app.state.engine = engine
        app.state.session_factory = session_factory
        app.state.jobs = jobs_store
        app.state.cooldowns = cooldowns_store
        app.state.providers = providers
        app.state.dispatcher = dispatcher

        worker_task = asyncio.create_task(
            failover_loop(dispatcher, scan_interval_s=config.failover_scan_interval_seconds)
        )
        logger.info(
            "Pixelrelay gateway ready — providers=%s, db=%s",
            list(providers.keys()),
            _safe_db_label(config.database_url),
        )

        try:
            yield
        finally:
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass
            await engine.dispose()

    app = FastAPI(
        title="Pixelrelay Gateway",
        version="0.2.0",
        description="Self-hosted failover gateway for generative media APIs.",
        lifespan=lifespan,
    )

    auth_dep = make_auth_dependency(config)
    app.include_router(health_router)
    app.include_router(callbacks_router)  # callbacks are auth'd via signature, not bearer
    app.include_router(make_generate_router(auth_dep))

    return app


def _safe_db_label(url: str) -> str:
    if "@" in url:
        return url.split("@", 1)[1]
    return url
