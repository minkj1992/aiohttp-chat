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

jinja_env = jinja2.Environment(loader=jinja2.PackageLoader('mychat', 'templates'))


async def index(request: web.Request) -> web.Response:
    """Set user_id to session and template's body,
     if not exist it randomly creates a hex_token for user_id """

    template = jinja_env.get_template('index.html')
    session = await get_session(request)
    user_id = session.get('user_id')
    if user_id is None:
        user_id = f'user-{secrets.token_hex(8)}'
        session['user_id'] = user_id
    content = template.render({
        'user_id': user_id,
    })
    return web.Response(status=200, body=content, content_type='text/html')


async def chat_send(request: web.Request) -> web.Response:
    """Send json response to client and Publish chat_data to Redis"""

    session = await get_session(request)
    user_id = session.get('user_id')
    if user_id is None:
        return web.json_response(status=401, data={'status': 'unauthorized'})

    payload = await request.json()
    chat_data = json.dumps({
        'user': user_id,
        'time': datetime.utcnow().isoformat(),
        'text': payload['text'],
    })
    await request.app['redis'].publish('chat', chat_data)
    return web.json_response(status=200, data={'status': 'ok'})


async def chat_subscribe(request: web.Request) -> web.Response:
    session = await get_session(request)
    user_id = session.get('user_id')
    if user_id is None:
        return web.json_response(status=401, data={'status': 'unauthorized'})

    msg_queue = asyncio.Queue()
    request_id = f'req-{session.token_hex(8)}'
    app['subscribers'][request_id] = asyncio.current_task()
    print(f'subscriber {user_id}:{request_id} started, tid={asyncio.current_task()}')  # TODO: io tasks log to queue?

    try:
        channels = await app['redis'].subscribe('chat')
        assert len(channels) == 1
        channel = channels[0]  # TODO: Redis default?

        async with sse_response(request) as response:
            async for chat_data in channel.iter():
                chat_data = chat_data.decode('utf8')
                print(f'subscriber {user_id}:{request_id} recv ', chat_data)
                if chat_data is None:
                    break
                await response.send(chat_data)
        return response

    # Python 3.8에서 변경: asyncio.CancelledError는 이제 BaseException의 서브 클래스.
    except Exception as e:
        traceback.print_exc()
    finally:
        print(f'subscriber {user_id}:{request_id} terminated')
        del app['subscribers'][request_id]


async def app_init(app):
    app['subscribers'] = {}
    app['redis_addr'] = ('localhost', 6379)  # TODO: dev ver

    # db: num of database instance(only 1) -> SELECT 0
    # Publisher
    app['redis'] = await aioredis.create_redis_pool(app['redis_addr'], db=0)  # TODO: loop를 주지 않는 이유는?

    # Subscriber
    session_storage = RedisStorage(
        await aioredis.create_redis_pool(app['redis_addr'], db=1),  # TODO: (search) 왜 redis_pool 을 2번 생성할까? -> pub/sub?
        max_age=3600
    )
    setup_session(app, session_storage)


async def app_shutdown(app):
    subscribers = [*app['subscribers'].values()]
    for subscriber in subscribers:
        subscriber.cancel()
    await asyncio.gather(*subscribers)  # 잔류 데이터 처리
    app['redis'].close()  # 스트림과 하부 소켓 Close
    await app['reids'].wait_closed()  # 스트림이 닫힐 때까지 기다립니다. 하부 연결이 닫힐 때까지 기다리려면 close() 뒤에 호출해야 합니다.


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
