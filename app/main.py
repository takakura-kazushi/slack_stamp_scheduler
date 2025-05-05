from fastapi import FastAPI, Request
from slack_sdk.web import WebClient
import os
from dotenv import load_dotenv
import logging
import dateparser
from datetime import datetime
import re
from datetime import timedelta

# ロギングの設定
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()
app = FastAPI()
slack_client = WebClient(token=os.getenv("SLACK_BOT_TOKEN"))

# 候補日投稿メッセージの情報を保持する辞書
# {message_ts: {"channel": channel_id, "options": {emoji: datetime}}}
candidate_messages = {}

def clean_datetime_text(text):
    """日時文字列の前処理を行う"""
    # 全角コロンを半角に変換
    text = text.replace('：', ':')
    
    # 全角数字を半角に変換
    text = text.translate(str.maketrans('１２３４５６７８９０', '1234567890'))
    
    # 曜日表現を削除
    text = re.sub(r'（[月火水木金土日]）', '', text)  # 全角括弧
    text = re.sub(r'\([月火水木金土日]\)', '', text)  # 半角括弧
    text = re.sub(r'[月火水木金土日]曜日', '', text)
    
    # 平仮名をすべて削除
    text = re.sub(r'[ぁ-ん]', '', text)
    
    # 月日を抽出して「5/6」形式に
    m = re.search(r'(\d{1,2})月(\d{1,2})日', text)
    date_part = ''
    if m:
        date_part = f"{m.group(1)}/{m.group(2)}"
    
    # 時刻を抽出して「18:30」形式に
    time_part = ''
    
    # 「18時半」→「18:30」
    t = re.search(r'(\d{1,2})時半', text)
    if t:
        time_part = f"{t.group(1)}:30"
    else:
        # 「18時30分」→「18:30」
        t = re.search(r'(\d{1,2})時(\d{1,2})分', text)
        if t:
            time_part = f"{t.group(1)}:{t.group(2).zfill(2)}"
        else:
            # 「18時」→「18:00」
            t = re.search(r'(\d{1,2})時', text)
            if t:
                time_part = f"{t.group(1)}:00"
            else:
                # 「18:00」形式の時刻を抽出
                t = re.search(r'(\d{1,2}):(\d{2})', text)
                if t:
                    time_part = f"{t.group(1)}:{t.group(2)}"
    
    # 両方あればスペースで連結
    if date_part and time_part:
        return f"{date_part} {time_part}"
    elif date_part:
        return date_part
    elif time_part:
        return time_part
    else:
        return text.strip()

def extract_datetime(text):
    """テキストから日時を抽出する"""
    # 日時文字列の前処理
    cleaned_text = clean_datetime_text(text)
    
    # 現在時刻を取得
    now = datetime.now()
    
    # 日付と時刻のパターンを定義
    date_patterns = [
        r'(\d{1,2})/(\d{1,2})',  # 5/6
        r'(\d{4})/(\d{1,2})/(\d{1,2})',  # 2024/5/6
        r'(\d{1,2})月(\d{1,2})日',  # 5月6日
        r'(\d{4})年(\d{1,2})月(\d{1,2})日',  # 2024年5月6日
    ]
    
    time_patterns = [
        r'(\d{1,2}):(\d{2})',  # 18:30
        r'(\d{1,2})時(\d{1,2})分',  # 18時30分
        r'(\d{1,2})時半',  # 18時半
        r'(\d{1,2})時',  # 18時
    ]
    
    # 日付と時刻を抽出
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
    
    # 日付と時刻を解析
    year = now.year
    month = now.month
    day = now.day
    hour = 0
    minute = 0
    
    if date_match:
        if len(date_match.groups()) == 2:  # 5/6 または 5月6日
            month = int(date_match.group(1))
            day = int(date_match.group(2))
        else:  # 2024/5/6 または 2024年5月6日
            year = int(date_match.group(1))
            month = int(date_match.group(2))
            day = int(date_match.group(3))
    
    if time_match:
        if len(time_match.groups()) == 2:  # 18:30 または 18時30分
            hour = int(time_match.group(1))
            minute = int(time_match.group(2))
        else:  # 18時半 または 18時
            hour = int(time_match.group(1))
            if '半' in time_match.group(0):
                minute = 30
    
    # 日時オブジェクトを作成
    try:
        dt = datetime(year, month, day, hour, minute)
        # 過去の日時は来年に設定
        if dt < now:
            dt = dt.replace(year=dt.year + 1)
        return dt
    except ValueError:
        return None

def normalize_emoji(emoji):
    """絵文字を正規化する"""
    # 数字の絵文字を正規化
    number_map = {
        '1': 'one',
        '2': 'two',
        '3': 'three',
        '4': 'four',
        '5': 'five',
        '6': 'six',
        '7': 'seven',
        '8': 'eight',
        '9': 'nine',
        '0': 'zero'
    }
    
    # 数字のみの場合は変換
    if emoji.isdigit():
        return number_map.get(emoji, emoji)
    return emoji

def extract_datetime_options(text):
    """メッセージから候補日時を抽出する"""
    options = {}
    
    # 絵文字を含む行を抽出
    # 例: ":1:：5/29(土)"
    lines = text.split('\n')
    for line in lines:
        # 絵文字を含む行のみを処理
        if re.search(r':\w+:', line):
            # 絵文字と日時を抽出
            match = re.search(r':(\w+):\s*[:：]\s*([^\n]+)', line)
            if match:
                emoji, datetime_str = match.groups()
                
                # 絵文字を正規化
                normalized_emoji = normalize_emoji(emoji)
                
                # 日時文字列の前処理
                datetime_str = clean_datetime_text(datetime_str)
                
                # 日時を解析
                dt = extract_datetime(datetime_str)
                if dt:
                    options[f":{normalized_emoji}:"] = dt
                    logger.info(f"日時を抽出しました: {datetime_str} -> {dt}")
                else:
                    logger.info(f"日時を解析できませんでした: {datetime_str}")
    
    return options

def normalize_reaction(reaction):
    """リアクションを正規化する"""
    # 数字のリアクションを正規化
    number_map = {
        '1': 'one',
        '2': 'two',
        '3': 'three',
        '4': 'four',
        '5': 'five',
        '6': 'six',
        '7': 'seven',
        '8': 'eight',
        '9': 'nine',
        '0': 'zero'
    }
    
    # 数字のみの場合は変換
    if reaction.isdigit():
        return number_map.get(reaction, reaction)
    return reaction

@app.post("/slack/events")
async def slack_events(req: Request):
    body = await req.json()

    # SlackのURL確認用
    if body.get("type") == "url_verification":
        return {"challenge": body["challenge"]}

    # イベントの検証
    if body.get("type") == "event_callback":
        event = body.get("event", {})
        
        # メンションイベントの処理
        if event.get("type") == "app_mention":
            user = event["user"]
            channel = event["channel"]
            text = event.get("text", "")
            message_ts = event.get("ts", "")
            thread_ts = event.get("thread_ts", None)

            # スレッド内での「:emoji:に決定します。」形式の決定メッセージか判定
            # スレッド内での決定メッセージ判定（「:emoji:」が含まれていればOK）
            if thread_ts:
                # メッセージ内のすべての:emoji:を抽出
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
                                decided.append((normalized_emoji,dt, dt_str))
                        if decided:
                            # 複数の場合はリスト形式で出力
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
                                text=f"指定されたスタンプに対応する日時が候補にありません。"
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

            
            # 候補日時を抽出
            options = extract_datetime_options(text)
            if options:
                # 候補日投稿メッセージとして記録
                candidate_messages[message_ts] = {
                    "channel": channel,
                    "options": options
                }
                logger.info(f"候補日投稿を記録しました: {message_ts}")
                logger.info(f"候補日時: {options}")
                
                # メンションに対する応答（ephemeralに変更）
                message = f"<@{user}> 候補日を記録しました！\n```"
                for emoji, dt in options.items():
                    message += f"{emoji}: {dt.strftime('%Y/%m/%d %H:%M')}\n"
                message += "```"
                
                slack_client.chat_postEphemeral(
                    channel=channel,
                    user=user,
                    text=message
                )
            else:
                logger.info(f"候補日時が見つかりませんでした: {text}")
        
        # リアクション追加イベントの処理
        elif event.get("type") == "reaction_added":
            # リアクションの詳細を取得
            reaction = event.get("reaction", "")
            user_id = event.get("user", "")
            item = event.get("item", {})
            message_ts = item.get("ts", "")
            channel_id = item.get("channel", "")
            
            # リアクションを正規化
            normalized_reaction = f":{normalize_reaction(reaction)}:"
            
            # 候補日投稿メッセージに対するリアクションかチェック
            if message_ts in candidate_messages:
                message_info = candidate_messages[message_ts]
                if normalized_reaction in message_info["options"]:
                    # ログ出力
                    logger.info(f"候補日投稿に対するリアクションが追加されました！")
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
                logger.info(f"候補日投稿以外のメッセージに対するリアクションは無視します")

    return {"ok": True}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
