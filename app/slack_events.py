from fastapi import APIRouter, Request
from slack_sdk.web import WebClient
import os, re, logging
from datetime import datetime, timedelta
from dotenv import load_dotenv


load_dotenv(verbose=True)
logger = logging.getLogger(__name__)
router = APIRouter()

# 環境変数が正しく設定されているか確認
bot_token = os.getenv("SLACK_BOT_TOKEN")
if not bot_token:
    raise ValueError("SLACK_BOT_TOKEN is not set in environment variables")
else:
    logger.info("SLACK_BOT_TOKEN is successfully loaded")

# Slack クライアント
slack_client = WebClient(token=bot_token)
# 候補日投稿メッセージの情報を保持する辞書
candidate_messages = {}

def clean_datetime_text(text):
    text = text.replace('：', ':')
    text = text.translate(str.maketrans('１２３４５６７８９０', '1234567890'))
    text = re.sub(r'（[月火水木金土日]）', '', text)
    text = re.sub(r'\([月火水木金土日]\)', '', text)
    text = re.sub(r'[月火水木金土日]曜日', '', text)
    text = re.sub(r'[ぁ-ん]', '', text)
    m = re.search(r'(\d{1,2})月(\d{1,2})日', text)
    date_part = f"{m.group(1)}/{m.group(2)}" if m else ''
    time_part = ''
    t = re.search(r'(\d{1,2})時半', text)
    if t:
        time_part = f"{t.group(1)}:30"
    else:
        t = re.search(r'(\d{1,2})時(\d{1,2})分', text)
        if t:
            time_part = f"{t.group(1)}:{t.group(2).zfill(2)}"
        else:
            t = re.search(r'(\d{1,2})時', text)
            if t:
                time_part = f"{t.group(1)}:00"
            else:
                t = re.search(r'(\d{1,2}):(\d{2})', text)
                if t:
                    time_part = f"{t.group(1)}:{t.group(2)}"
    if date_part and time_part:
        return f"{date_part} {time_part}"
    elif date_part:
        return date_part
    elif time_part:
        return time_part
    else:
        return text.strip()

def extract_datetime(text):
    cleaned_text = clean_datetime_text(text)
    now = datetime.now()
    date_patterns = [
        r'(\d{1,2})/(\d{1,2})',
        r'(\d{4})/(\d{1,2})/(\d{1,2})',
        r'(\d{1,2})月(\d{1,2})日',
        r'(\d{4})年(\d{1,2})月(\d{1,2})日',
    ]
    time_patterns = [
        r'(\d{1,2}):(\d{2})',
        r'(\d{1,2})時(\d{1,2})分',
        r'(\d{1,2})時半',
        r'(\d{1,2})時',
    ]
    date_match = None
    time_match = None
    for pattern in date_patterns:
        date_match = re.search(pattern, cleaned_text)
        if date_match:
            break
    for pattern in time_patterns:
        time_match = re.search(pattern, cleaned_text)
        if time_match:
            break
    year, month, day = now.year, now.month, now.day
    hour, minute = 0, 0
    if date_match:
        if len(date_match.groups()) == 2:
            month = int(date_match.group(1))
            day = int(date_match.group(2))
        else:
            year = int(date_match.group(1))
            month = int(date_match.group(2))
            day = int(date_match.group(3))
    if time_match:
        if len(time_match.groups()) == 2:
            hour = int(time_match.group(1))
            minute = int(time_match.group(2))
        else:
            hour = int(time_match.group(1))
            if '半' in time_match.group(0):
                minute = 30
    try:
        dt = datetime(year, month, day, hour, minute)
        if dt < now:
            dt = dt.replace(year=dt.year + 1)
        return dt
    except ValueError:
        return None

def normalize_emoji(emoji):
    number_map = {
        '1': 'one', '2': 'two', '3': 'three', '4': 'four',
        '5': 'five', '6': 'six', '7': 'seven', '8': 'eight',
        '9': 'nine', '0': 'zero'
    }
    if emoji.isdigit():
        return number_map.get(emoji, emoji)
    return emoji

def normalize_reaction(reaction):
    number_map = {
        '1': 'one', '2': 'two', '3': 'three', '4': 'four',
        '5': 'five', '6': 'six', '7': 'seven', '8': 'eight',
        '9': 'nine', '0': 'zero'
    }
    if reaction.isdigit():
        return number_map.get(reaction, reaction)
    return reaction

def extract_datetime_options(text):
    options = {}
    lines = text.split('\n')
    for line in lines:
        if re.search(r':\w+:', line):
            match = re.search(r':(\w+):\s*[:：]\s*([^\n]+)', line)
            if match:
                emoji, datetime_str = match.groups()
                normalized_emoji = normalize_emoji(emoji)
                datetime_str = clean_datetime_text(datetime_str)
                dt = extract_datetime(datetime_str)
                if dt:
                    options[f":{normalized_emoji}:"] = dt
                    logger.info(f"日時を抽出しました: {datetime_str} -> {dt}")
                else:
                    logger.info(f"日時を解析できませんでした: {datetime_str}")
    return options

@router.post("/slack/events")
async def handle_slack_events(req: Request):
    body = await req.json()
    if body.get("type") == "url_verification":
        return {"challenge": body["challenge"]}
    if body.get("type") == "event_callback":
        event = body.get("event", {})
        if event.get("type") == "app_mention":
            user = event["user"]
            channel = event["channel"]
            text = event.get("text", "")
            message_ts = event.get("ts", "")
            thread_ts = event.get("thread_ts", None)
            if thread_ts:
                emoji_matches = re.findall(r'(:\w+:)', text)
                if emoji_matches:
                    candidate = candidate_messages.get(thread_ts)
                    if candidate:
                        decided = []
                        for emoji in emoji_matches:
                            normalized_emoji = emoji
                            m = re.match(r":(\w+):", emoji)
                            if m:
                                normalized_emoji = f":{normalize_emoji(m.group(1))}:"
                            dt = candidate["options"].get(normalized_emoji)
                            if dt:
                                dt_str = dt.strftime('%Y/%m/%d %H:%M')
                                decided.append((normalized_emoji, dt, dt_str))
                        if decided:
                            if len(decided) == 1:
                                emoji, dt_obj, dt_str = decided[0]
                                if dt_obj.hour < 8:
                                    reminder_dt = dt_obj.replace(hour=8, minute=0) - timedelta(days=1)
                                else:
                                    reminder_dt = dt_obj.replace(hour=8, minute=0)
                                reminder_str = reminder_dt.strftime('%Y/%m/%d %H:%M')
                                msg = (f"日時を\n{emoji} {dt_str}\nに決定しました。\n"
                                       f"{reminder_str}にリマインドします。")
                            else:
                                msg = "日時を\n"
                                for emoji, dt_obj, dt_str in decided:
                                    if dt_obj.hour < 8:
                                        reminder_dt = dt_obj.replace(hour=8, minute=0) - timedelta(days=1)
                                    else:
                                        reminder_dt = dt_obj.replace(hour=8, minute=0)
                                    reminder_str = reminder_dt.strftime('%Y/%m/%d %H:%M')
                                    msg += f"{emoji} {dt_str} （リマインド: {reminder_str}）\n"
                                msg += "に決定しました。"
                            slack_client.chat_postMessage(
                                channel=channel,
                                text=msg,
                                thread_ts=thread_ts
                            )
                            logger.info(f"日程決定: {decided}")
                        else:
                            slack_client.chat_postEphemeral(
                                channel=channel,
                                user=user,
                                text="指定されたスタンプに対応する日時が候補にありません。"
                            )
                            logger.info(f"決定スタンプが候補にありません: {emoji_matches}")
                    else:
                        slack_client.chat_postEphemeral(
                            channel=channel,
                            user=user,
                            text="このスレッドは候補日投稿ではありません。"
                        )
                        logger.info("スレッドが候補日投稿ではありません")
                    return {"ok": True}
            options = extract_datetime_options(text)
            if options:
                candidate_messages[message_ts] = {"channel": channel, "options": options}
                logger.info(f"候補日投稿を記録しました: {message_ts}")
                logger.info(f"候補日時: {options}")
                message = f"<@{user}> 候補日を記録しました！\n```"
                for emoji, dt in options.items():
                    message += f"{emoji}: {dt.strftime('%Y/%m/%d %H:%M')}\n"
                message += "```"
                slack_client.chat_postEphemeral(channel=channel, user=user, text=message)
            else:
                logger.info(f"候補日時が見つかりませんでした: {text}")
        elif event.get("type") == "reaction_added":
            reaction = event.get("reaction", "")
            user_id = event.get("user", "")
            item = event.get("item", {})
            message_ts = item.get("ts", "")
            channel_id = item.get("channel", "")
            normalized_reaction = f":{normalize_reaction(reaction)}:"
            if message_ts in candidate_messages:
                message_info = candidate_messages[message_ts]
                if normalized_reaction in message_info["options"]:
                    logger.info("候補日投稿に対するリアクションが追加されました！")
                    logger.info(f"ユーザー: {user_id}")
                    logger.info(f"スタンプ: {normalized_reaction}")
                    logger.info(f"選択された日時: {message_info['options'][normalized_reaction]}")
                    logger.info(f"メッセージTS: {message_ts}")
                    logger.info(f"チャンネルID: {channel_id}")
                else:
                    logger.info(f"無効なスタンプが押されました: {reaction}")
                    logger.info(f"正規化後: {normalized_reaction}")
                    logger.info(f"有効なスタンプ: {list(message_info['options'].keys())}")
            else:
                logger.info("候補日投稿以外のメッセージに対するリアクションは無視します")
    return {"ok": True}