import logging
import asyncio
import functools
import traceback
import click
from aiohttp import web
from aiohttp_session import setup as setup_session, get_session
from aiohttp_session.redis_storage import RedisStorage
from aiohttp_sse import sse_response
import aioredis
from datetime import datetime
import jinja2
import json
import secrets

jinja_environment = jinja2.Environment(
    loader=jinja2.FileSystemLoader('./templates/')
)


def auth_required(handler):
    @functools.wraps(handler)
    async def wrapped(request: web.Request) -> web.Response:
        session = await get_session(request)
        user_id = session.get('user_id')
        if user_id is None:
            return web.json_response(status=401, data={'status': 'unauthorized'})

        return await handler(request, user_id)

    return wrapped


async def index(request: web.Request) -> web.Response:
    """Set user_id to session and template's body,
     if not exist it randomly creates a hex_token for user_id """

    template = jinja_environment.get_template('index.html')
    session = await get_session(request)
    user_id = session.get('user_id')
    if user_id is None:
        user_id = f'user-{secrets.token_hex(8)}'
        session['user_id'] = user_id
    content = template.render({
        'impl_type': request.app['impl_type'],
        'user_id': user_id,
    })

    return web.Response(status=200, body=content, content_type='text/html')


@auth_required
async def chat_send(request: web.Request, user_id: str) -> web.Response:
    """Send json response to client and Publish chat_data to Redis"""

    payload = await request.json()
    chat_data = json.dumps({
        'user': user_id,
        'time': datetime.utcnow().isoformat(),
        'text': payload['text'],
    })
    await request.app['redis'].publish('chat', chat_data)
    return web.json_response(status=200, data={'status': 'ok'})


@auth_required
async def chat_subscribe(request: web.Request, user_id: str) -> web.Response:
    msg_queue = asyncio.Queue()
    request_id = f'ssereq-{secrets.token_hex(8)}'
    request.app['client_queues'][request_id] = msg_queue
    print(f'subscriber {user_id}:{request_id} started')

    try:
        async with sse_response(request) as response:
            while True:
                chat_data = await msg_queue.get()
                print('sse.recv', chat_data)
                if chat_data is None:
                    break
                await response.send(json.dumps(chat_data))
        return response

    # Python 3.8에서 변경: asyncio.CancelledError는 이제 BaseException의 서브 클래스.
    except Exception:
        traceback.print_exc()
    finally:
        print(f'subscriber {user_id}:{request_id} terminated')
        del request.app['client_queues'][request_id]


@auth_required
async def chat_websocket(request: web.Request, user_id: str) -> web.Response:
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    request_id = f'wsreq-{secrets.token_hex(8)}'
    my_queue = asyncio.Queue()
    request.app['client_queues'][request_id] = my_queue

    async def chat_recv():
        try:
            while True:
                chat_record = await my_queue.get()
                print('ws.recv', chat_record)
                if chat_record is None:
                    break
                await ws.send_json(chat_record)
        except asyncio.CancelledError:
            pass

    recv_task = asyncio.create_task(chat_recv())
    print(f'subscriber {user_id}:{request_id} started')
    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                payload = json.loads(msg.data)
                chat_record = json.dumps({
                    'user': user_id,
                    'time': datetime.utcnow().isoformat(),
                    'text': payload['text'],
                })
                await request.app['redis'].publish('chat', chat_record)
        return ws
    except asyncio.CancelledError:
        recv_task.cancel()
        await recv_task
        raise
    finally:
        print(f'subscriber {user_id}:{request_id} terminated')
        del request.app['client_queues'][request_id]


async def chat_distribute(app: web.Application) -> None:
    print('distributer started')
    redis = await aioredis.create_redis(app['redis_addr'], db=0)
    try:
        channels = await redis.subscribe('chat')
        assert len(channels) == 1
        channel = channels[0]
        async for chat_data in channel.iter():
            chat_data = json.loads(chat_data.decode('utf-8'))

            for queue in app['client_queues'].values():
                queue.put_nowait(chat_data)

    except Exception:
        traceback.print_exc()
    finally:
        # Logically, we need to "unsubscribe" the channel here,
        # but the "redis" connection is already kind-of corrupted
        # due to cancellation.
        # Just terminate our coroutine and let the Redis server
        # to recognize connection close as the signal of unsubscribe.
        print('distributer terminated')


async def app_init(app):
    app['client_queues'] = {}
    app['redis_addr'] = ('localhost', 6379)  # TODO: dev ver

    # db: num of database instance(only 1) -> SELECT 0
    # Publisher
    app['redis'] = await aioredis.create_redis_pool(app['redis_addr'], db=0)  # TODO: loop를 주지 않는 이유는?

    # Subscriber
    session_storage = RedisStorage(
        await aioredis.create_redis_pool(app['redis_addr'], db=1),  # TODO: (search) 왜 redis_pool 을 2번 생성할까? -> pub/sub?
        max_age=3600,
    )
    setup_session(app, session_storage)
    app['distributer'] = asyncio.create_task(chat_distribute(app))


async def app_shutdown(app):
    client_queues = [*app['client_queues'].values()]
    for queue in client_queues:
        queue.put_nowait(None)
    app['distributer'].cancel()
    await app['distributer']
    app['redis'].close()  # 스트림과 하부 소켓 Close
    await app['reids'].wait_closed()  # 스트림이 닫힐 때까지 기다립니다. 하부 연결이 닫힐 때까지 기다리려면 close() 뒤에 호출해야 합니다.


@click.command()
@click.option('-h', '--host', default='127.0.0.1')
@click.option('-p', '--port', default=8080)
@click.option('-t', '--impl-type', type=click.Choice(['sse', 'websocket']), default='websocket')
def main(host, port, impl_type):
    app = web.Application()
    logging.basicConfig(level=logging.DEBUG)
    app['impl_type'] = impl_type
    app.add_routes([
        web.get("/", index),
        web.get("/chat", chat_subscribe),
        web.get("/chat-ws", chat_websocket),
        web.post("/chat", chat_send),
    ])

    app.on_startup.append(app_init)
    app.on_shutdown.append(app_shutdown)
    web.run_app(app, host=host, port=port)


if __name__ == '__main__':
    main()
