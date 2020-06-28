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
    app['subscribers'] = {}
    app['redis_addr'] = ('localhost', 6379)  # TODO: dev ver

    # db: num of database instance(only 1) -> SELECT 0
    # Subscriber
    app['redis'] = await aioredis.create_redis_pool(app['redis_addr'], db=0)  # TODO: loop를 주지 않는 이유는?

    # Publisher
    session_storage = RedisStorage(
        await aioredis.create_redis_pool(app['redis_addr'], db=1),  # TODO: (search) 왜 redis_pool 을 2번 생성할까? -> pub/sub?
        max_age=3600
    )
    setup_session(app, session_storage)


async def app_shutdown(app):
    subscribers = [*app['subscribers'].values()]
    for subscriber in subscribers:
        subscriber.cancel()
    await asyncio.gather(*subscribers) # 잔류 데이터 처리
    app['redis'].close() # 스트림과 하부 소켓 Close
    await app['reids'].wait_closed() # 스트림이 닫힐 때까지 기다립니다. 하부 연결이 닫힐 때까지 기다리려면 close() 뒤에 호출해야 합니다.

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
