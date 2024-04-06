from httpx import AsyncClient, Response
from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent
from nonebot.matcher import Matcher
from nonebot.typing import T_State

main: int = 0
subs: list[str] = [""]


async def event_filter(bot: Bot, group_id: int):
    resp = await bot.get_group_member_list(group_id=group_id)
    if bot.self_id in subs and main in [i["user_id"] for i in resp]:
        return True


worker = on_message(block=False)

COMMANDS = ["开始钓鱼", "结束钓鱼", "停止钓鱼", "钓鱼记录", "钓鱼统计"]

URL = "http://localhost:8000/chat_fishing"
client = AsyncClient()


@worker.handle()
async def _(bot: Bot, event: MessageEvent, matcher: Matcher, state: T_State):
    if isinstance(event, GroupMessageEvent) and await event_filter(bot, event.group_id):
        matcher.stop_propagation()
        return
    text = event.message.extract_plain_text()
    if not text:
        return
    data = {"user_id": f"OneBot 11_{event.user_id}", "message": text}
    try:
        resp: Response = await client.post(URL, json=data, timeout=60)
    except:
        return
    if not text in COMMANDS:
        return
    if resp.status_code == 200:
        message = resp.text
        if not message:
            return
        if "header" in state:
            message = "".join([state["header"], message])
        await worker.send(Message(message))
        matcher.stop_propagation()
