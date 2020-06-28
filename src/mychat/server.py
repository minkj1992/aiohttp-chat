import asyncio
import traceback
from aiohttp import web
from aiohttp_session import setup as setup_session, get_session
from aiohttp_session.redis_storage import RedisStorage
from aiohttp_sse import sse_response
import aioredis
from datetime import datetime
import jinja2
import json
import secrets

jenv = jinja2.Environment(loader=jinja2.PackageLoader('mychat', 'templates'))


async def index():
    pass


async def chat_subscribe():
    pass


async def chat_send():
    pass


async def app_init(app):
    pass


async def app_shutdown(app):
    pass


if __name__ == '__main__':
    app = web.Application()
    app.add_routes([
        web.get("/", index),
        web.get("/chat", chat_subscribe),
        web.post("/chat", chat_send),
    ])

    app.on_startup.append(app_init)
    app.on_shutdown.append(app_shutdown)
    web.run_app(app, host='127.0.0.1', port=8080)
