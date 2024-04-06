ROUTER = "/chat_fishing"
FUNCTION_NAME = "chat_fishing"
DISPLAY_NAME = "钓鱼"

from dataclasses import dataclass
from random import choice, randint, random
from re import match
from time import localtime, strftime, time

from fastapi import BackgroundTasks
from fastapi.responses import PlainTextResponse
from httpx import AsyncClient
from internal.addons.scheduler import scheduler
from internal.addons.statistics import increment
from internal.addons.users import FetchData, UploadData, conn, fetch, upload
from internal.config import config
from internal.constants import SQL_INTERNAL_ADDONS_USERS_GET_USER_BY_USERNAME
from internal.driver import app


@app.get(ROUTER, response_class=PlainTextResponse, tags=[DISPLAY_NAME])
async def info():
    """
    # 钓鱼

    一种群聊友好的静默文字游戏。

    平台用户通过发送 `开始钓鱼` 开始游戏，期间服务端监听由客户端上报的该用户的所有消息。

    每监听到一条消息，都会有一定概率触发鱼咬钩事件，此时根据消息的 **长度** 判断鱼是否上钩或者跑掉。

    之后再回到监听状态，并重复该过程，直到平台用户发送 `结束钓鱼` 结束游戏。

    用户发送 `结束钓鱼` 后，会返回用户本次钓鱼的结果，包括钓到的鱼的数量和长度记录更新。

    用户发送 `钓鱼统计` 可以查看自己的钓鱼统计数据。

    这个路由仅用于功能可用性测试，且不会记录被访问的次数。
    """


SAKANA = {
    "香鱼": {"钓鱼力": 1.00, "长度区间": [[950, 1600]], "单位": "条"},
    # https://www.baike.com/wikiid/7226946812584820796
    "红点鲑": {"钓鱼力": 1.00, "长度区间": [[1200, 3000]], "单位": "条"},
    # https://baike.baidu.com/item/远东红点鲑
    "虹鳟": {"钓鱼力": 1.04, "长度区间": [[5100, 7600]], "单位": "条"},
    # https://zh.wikipedia.org/zh-cn/虹鱒
    "鲶鱼": {"钓鱼力": 1.08, "长度区间": [[5000, 8000]], "单位": "条"},
    # https://www.baike.com/wikiid/8720900259654510725
    "鲫鱼": {"钓鱼力": 1.04, "长度区间": [[460, 2550]], "单位": "条"},
    # https://baike.baidu.com/item/鲫
    "泥鳅": {"钓鱼力": 1.00, "长度区间": [[1500, 3600]], "单位": "条"},
    # https://rf4game.com/forumcn/index.php?/topic/554-泥鳅鱼/
    "鲑鱼": {"钓鱼力": 1.04, "长度区间": [[1200, 3000]], "单位": "条"},
    "鳕鱼": {"钓鱼力": 1.08, "长度区间": [[3000, 9000]], "单位": "条"},
    # http://www.americanseafoods.com.cn/fish/show.php?lang=cn&id=106
    "河鲀": {"钓鱼力": 1.08, "长度区间": [[1000, 3000]], "单位": "条"},
    # http://www.kepu.net.cn/gb/lives/fish/import/200210230080.html
    "鱿鱼": {"钓鱼力": 1.04, "长度区间": [[2200, 2500]], "单位": "条"},
    # https://zhuanlan.zhihu.com/p/149089800
    "章鱼": {"钓鱼力": 1.12, "长度区间": [[4200, 6000]], "单位": "条"},
    # https://baike.baidu.com/item/普通章鱼
    "裙带菜": {"钓鱼力": 1.00, "长度区间": [[10, 6000]], "单位": "条"},
    # https://baike.baidu.com/item/裙带菜
    "扇贝": {"钓鱼力": 1.00, "长度区间": [[1300, 7400]], "单位": "只"},
    # https://www.baike.com/wikiid/5279728052053621234
    "青花鱼": {"钓鱼力": 1.12, "长度区间": [[3000, 5000]], "单位": "条"},
    # https://zh.wikipedia.org/wiki/鯖屬
    "沙丁鱼": {"钓鱼力": 1.12, "长度区间": [[1500, 3000]], "单位": "条"},
    # https://baike.baidu.com/item/沙丁鱼
    "鲷鱼": {"钓鱼力": 1.12, "长度区间": [[1500, 3000]], "单位": "条"},
    # https://baike.baidu.com/item/真鲷鱼
}


@dataclass
class MessageEvent:
    user_id: str
    message: str


@app.post(ROUTER, response_class=PlainTextResponse, tags=[DISPLAY_NAME])
async def main(event: MessageEvent, *, autofish: bool = False):
    """
    # 钓鱼

    钓鱼游戏的主要逻辑。

    ## 参数

    - `event: MessageEvent`：消息事件，包括用户 ID 和消息内容。
    """
    # 从数据库获取用户密码和用户数据
    data, password = await fetch_userdata(event)
    # 获取用户的游戏状态
    state = get_state(data)

    if start(event) and not state["游戏中"]:
        state["游戏中"] = strtime()
        state["钓鱼次数"] += 1
        s = "你开始钓鱼了。"
        if not state["开始游戏时间"]:
            state["开始游戏时间"] = strtime()
            s = s[:-1] + "，这是你第一次钓鱼。\n\n可以随时 “结束钓鱼”"
        write_log(state, s)
        await upload(UploadData(event.user_id, password, str(state), DISPLAY_NAME))
        increment(ROUTER)
        return s
    elif stop(event) and state["游戏中"]:
        state["游戏中"] = None
        s = "你结束了钓鱼，"
        count = state["经过的消息数量"]
        if count:
            s += f"经过 {count} 条消息后，"
        if not state["钓到的鱼"]:
            s += "什么都没发生。"
            if not state["第一次空军时间"]:
                state["第一次空军时间"] = strtime()
                s += "这是你第一次空军。"
            state["空军次数"] += 1
        else:
            s += "你钓到了 "
        counts = {i: 0 for i in SAKANA}
        new_max = {}
        new_min = {}
        for i in state["钓到的鱼"]:
            k, v, t = i["名称"], i["长度"], i["上钩时间"]
            zukan = {
                "条数": 0,
                "最大长度记录": 0,
                "最大长度记录时间": 0,
                "最小长度记录": 0,
                "最小长度记录时间": 0,
            }
            zukan.update(state["钓鱼图鉴"].get(k, {}))
            zukan["条数"] += 1
            if v > zukan["最大长度记录"]:
                zukan["最大长度记录"] = v
                zukan["最大长度记录时间"] = t
                new_max[k] = v
            if v < zukan["最小长度记录"] or not zukan["最小长度记录"]:
                zukan["最小长度记录"] = v
                zukan["最小长度记录时间"] = t
                new_min[k] = v
            state["钓鱼图鉴"][k] = zukan
            counts[k] += 1
        for k, v in {k: v for k, v in counts.items()}.items():
            if not v:
                del counts[k]
        if state["钓到的鱼"]:
            l = []
            for k, v in counts.items():
                l.append(f"{v} {SAKANA[k]['单位']}{k}")
            s += "、".join(l)
            c = sum(counts.values())
            if c > 1:
                s += f"，共计 {c} 条鱼"
            s += "。"
            wasted = state["被鱼跑掉的次数"]
            if wasted:
                s = s[:-1] + f"，被鱼跑掉了 {wasted} 次。"
        # ========== 状态重置 ==========
        state["累计的消息数量"] += state["经过的消息数量"]
        state["经过的消息数量"] = 0
        state["我的鱼篓"] += state["钓到的鱼"]
        state["钓到的鱼"] = []
        state["被鱼跑掉的总次数"] += state["被鱼跑掉的次数"]
        state["被鱼跑掉的次数"] = 0
        # ========== 状态重置 ==========
        if new_max or new_min:
            s += "\n"
            for i in SAKANA:
                zukan = state["钓鱼图鉴"].get(i, {})
                if i in new_min or i in new_max:
                    s += f"\n{i}的长度记录更新为 {round(zukan['最小长度记录'] / 10, 1)}~{round(zukan['最大长度记录'] / 10, 1)} mm！"
        d = {}
        for i in SAKANA:
            if i in state["钓鱼图鉴"]:
                d[i] = state["钓鱼图鉴"][i]
        state["钓鱼图鉴"] = d
        write_log(state, s)
        await upload(UploadData(event.user_id, password, str(state), DISPLAY_NAME))
        increment(ROUTER)
        return s
    elif stat(event):
        counts = {i: 0 for i in SAKANA}
        max = {}
        min = {}
        for k, v in state["钓鱼图鉴"].items():
            counts[k] += v["条数"]
            if not max or v["最大长度记录"] > max[list(max.keys())[0]]["最大长度记录"]:
                max = {k: v}
            if not min or v["最小长度记录"] < min[list(min.keys())[0]]["最小长度记录"]:
                min = {k: v}
        for k, v in {k: v for k, v in counts.items()}.items():
            if not v:
                del counts[k]
        if not state["开始游戏时间"]:
            s = "你还没有钓过鱼。"
            increment(ROUTER)
            return s
        s = f"你于 {state['开始游戏时间']} 开始了第一次钓鱼，"
        fb = state["第一次钓到鱼"]
        if fb:
            fb_result = state["第一次钓到的鱼"]
            s += f"第一次钓到鱼是在 {fb} 钓到的{fb_result['名称']}，长度为 {round(fb_result['长度'] / 10, 1)} mm。"
        else:
            s_ = "你从未钓到过鱼。"
            if state["被鱼跑掉的总次数"]:
                s += (
                    "虽然"
                    + s_[:-1]
                    + "，但你累计放生了 "
                    + str(state["被鱼跑掉的总次数"])
                    + " 条鱼。"
                )
        # s += "\n"
        if fb:
            if state["钓鱼图鉴"]:
                s += "至今总共钓到 "
                l = []
                c = 0
                for k, v in counts.items():
                    l.append(f"{v} {SAKANA[k]['单位']}{k}")
                    c += v
                s += "、".join(l)
                if c > 1:
                    s += f"，共计 {c} 条鱼"
                s += "。"
            if state["被鱼跑掉的总次数"]:
                s = s[:-1] + f"，被鱼跑掉了 {state['被鱼跑掉的总次数']} 次。"
        if state["第一次空军时间"]:
            s += f"你的第一次空军是在 {state['第一次空军时间']}，一共空军了 {state['空军次数']} 次。"
        elif not fb:
            s += "你还不懂什么叫空军。"
        else:
            s += "你从未空军。"
        # s += "\n"
        if min and max != min:
            k, v = list(min.items())[0]
            s += f"你钓到过最小的鱼是仅长 {round(v['最小长度记录'] / 10, 1)} mm 的{k}。"
            if max:
                s = s[:-1] + "，而"
        if max:
            k, v = list(max.items())[0]
            s += (
                f"你钓到过最大的鱼是{k}，竟长达 {round(v['最大长度记录'] / 10, 1)} mm！"
            )
        increment(ROUTER)
        return s
    elif state["游戏中"]:
        if not autofish:
            state["经过的消息数量"] += 1
        if fish_bites(state):
            if caught(event):
                result = roll(state)
                state["钓到的鱼"].append(result)
                s = f"你钓到了一条{result['名称']}，长度为 {round(result['长度']/10,1)} mm！"
                if not state["第一次钓到鱼"]:
                    state["第一次钓到鱼"] = strtime()
                    state["第一次钓到的鱼"] = result
            else:
                s = "鱼跑掉了..."
                state["被鱼跑掉的次数"] += 1
            # 重设钓鱼力
            state["钓鱼力"] = 1.00 + len(state["钓到的鱼"]) * 0.002
            write_log(state, s)
        else:
            s = "今天天气不错。"
            # 下一次鱼咬钩的概率上升
            state["钓鱼力"] += 0.001
        await upload(
            UploadData(event.user_id, password, str(state), DISPLAY_NAME), hot=True
        )
        if not autofish:
            increment(ROUTER)
        return s
    else:
        return "你没有在钓鱼。"


def get_state(data: dict):
    d = eval(str(data)) or {}
    # 总是用模板初始化用户数据
    state = {
        "游戏中": None,
        "钓鱼力": 1.00,
        "钓鱼次数": 0,
        "空军次数": 0,
        "钓到的鱼": [],
        "我的鱼篓": [],
        "钓鱼图鉴": {},
        "开始游戏时间": "",
        "第一次钓到鱼": "",
        "第一次钓到的鱼": {},
        "第一次空军时间": "",
        "经过的消息数量": 0,
        "累计的消息数量": 0,
        "被鱼跑掉的次数": 0,
        "被鱼跑掉的总次数": 0,
        "最近的 10000 条日志": [],
    }
    state.update(d)
    # ========== 从最近的 10000 条日志中将钓到的鱼加入到我的鱼篓 ==========
    if not state["我的鱼篓"] and state["钓鱼图鉴"]:
        for i in state["最近的 10000 条日志"]:
            m = match(
                r"\[(?P<time>.+)\] 你钓到了一条(?P<name>.+)，长度为 (?P<length>.+) mm！",
                i,
            )
            if not m:
                continue
            name = m.group("name")
            length = int(float(m.group("length")) * 10)
            time = m.group("time")
            state["我的鱼篓"].append({"名称": name, "长度": length, "上钩时间": time})
    # ========== 从最近的 10000 条日志中将钓到的鱼加入到我的鱼篓 ==========
    return state


async def fetch_userdata(event: MessageEvent):
    data = FetchData(event.user_id, namespace=DISPLAY_NAME)
    await fetch(data)
    row = conn.execute(
        SQL_INTERNAL_ADDONS_USERS_GET_USER_BY_USERNAME, (event.user_id,)
    ).fetchone()
    if row:
        password = row["密码"]
        data = FetchData(event.user_id, password, DISPLAY_NAME)

    data = (await fetch(data))["userdata"]
    return data, password


def roll(state: dict):
    while True:
        name = choice(list(SAKANA.keys()))
        data = SAKANA[name]
        if data["钓鱼力"] <= state["钓鱼力"]:
            break
    l = choice(data["长度区间"])
    length = randint(l[0], l[1])
    return {"名称": name, "长度": length, "上钩时间": strtime()}


def fish_bites(state: dict):
    return random() < 0.07 * state["钓鱼力"]


TEXT_SIZE = 140


def caught(event: MessageEvent):
    l = []
    while len(l) < TEXT_SIZE // 10 + 1:
        r = randint(1, TEXT_SIZE)
        if not r in l:
            l.append(r)
    return len(event.message) in l


def start(event: MessageEvent):
    return event.message == "开始钓鱼"


def stop(event: MessageEvent):
    return event.message in ("结束钓鱼", "停止钓鱼")


def stat(event: MessageEvent):
    return event.message in ("钓鱼记录", "钓鱼统计")


def strtime():
    return strftime("%Y-%m-%d %H:%M:%S", localtime(time()))


def write_log(state: dict, message: str):
    s = f"[{strtime()}] {message}"
    state["最近的 10000 条日志"].append(s)
    if len(state["最近的 10000 条日志"]) > 10000:
        state["最近的 10000 条日志"].pop(0)


ROUTER_TEST = ROUTER + "/test"


@app.post(ROUTER_TEST, response_class=PlainTextResponse, tags=[DISPLAY_NAME])
async def test(user_id: str = "Lan", target: int = 10):
    """
    # 钓鱼

    以钓到指定条数的鱼为目标，测试钓鱼游戏的运行。
    """
    message_0 = MessageEvent(user_id, "0" * randint(1, TEXT_SIZE))
    message_1 = MessageEvent(user_id, "开始钓鱼")
    message_2 = MessageEvent(user_id, "结束钓鱼")
    while True:
        data, _ = await fetch_userdata(message_0)
        state = get_state(data)
        if not state["游戏中"]:
            await main(message_1)
        if len(state["钓到的鱼"]) < target:
            await main(message_0)
        else:
            return await main(message_2)


ROUTER_AUTOFISH = ROUTER + "/autofish"


@app.post(ROUTER_AUTOFISH, response_class=PlainTextResponse, tags=[DISPLAY_NAME])
async def autofish(background_tasks: BackgroundTasks):
    """
    # 钓鱼

    AFK 自动钓鱼。

    如果因为故障导致错过了上一次触发，可以使用此路由补偿。
    """
    rows = conn.execute(
        "SELECT * FROM sqlite_master WHERE type='table' AND name='用户数据'"
    ).fetchall()
    if not rows:
        return
    cols = conn.execute("PRAGMA table_info(用户数据)").fetchall()
    if not DISPLAY_NAME in [col["name"] for col in cols]:
        return
    rows = conn.execute(f"SELECT 用户名 FROM 用户数据 WHERE {DISPLAY_NAME} IS NOT NULL")
    for row in rows:
        username = row["用户名"]
        message = MessageEvent(username, "0" * randint(1, 140))
        data, _ = await fetch_userdata(message)
        state = get_state(data)
        if state["游戏中"]:
            background_tasks.add_task(main, message, autofish=True)


@scheduler.scheduled_job("cron", minute=0)
@scheduler.scheduled_job("cron", minute=20)
@scheduler.scheduled_job("cron", minute=40)
async def autofish_():
    async with AsyncClient() as client:
        host = config.host
        port = config.port
        url = f"http://{host}:{port}{ROUTER_AUTOFISH}"
        await client.post(url)
