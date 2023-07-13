import os

import aioredis
from fastapi import FastAPI
from fastapi_admin.app import app as admin_app
from tortoise.contrib.fastapi import register_tortoise

from app.admin.auth import AuthProvider

from app.callbooker.views import cb_router
from app.settings import Settings
from app.tc2.views import tc2_router

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

from app.admin import resources, views  # noqa: E402

settings = Settings()

app = FastAPI()
app.mount('', admin_app)
register_tortoise(
    app,
    db_url=settings.pg_dsn,
    modules={'models': ['app.models']},
    generate_schemas=True,
    add_exception_handlers=True,
)
app.include_router(tc2_router, prefix='/tc2')
app.include_router(cb_router, prefix='/callbooker')


@app.on_event('startup')
async def startup():
    redis = await aioredis.from_url(settings.redis_dsn)
    await admin_app.configure(
        template_folders=[os.path.join(BASE_DIR, 'admin/templates/')],
        providers=[AuthProvider()],
        language_switch=False,
        redis=redis,
        admin_path='',
    )
    from app.utils import get_config

    await get_config()
