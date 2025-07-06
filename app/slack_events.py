from fastapi import APIRouter, Request
from slack_sdk.web import WebClient
import os, re, logging
from datetime import datetime, timedelta
import pytz
from dotenv import load_dotenv
from .db import supabase
from .scheduler import scheduler

load_dotenv(verbose=True)
logger = logging.getLogger(__name__)
router = APIRouter()

# 日本時間のタイムゾーンオブジェクトを定義
JST = pytz.timezone('Asia/Tokyo')

# 環境変数が正しく設定されているか確認
bot_token = os.getenv("SLACK_BOT_TOKEN")
if not bot_token:
    raise ValueError("SLACK_BOT_TOKEN is not set in environment variables")
else:
    logger.info("SLACK_BOT_TOKEN is successfully loaded")

# Slack クライアント
slack_client = WebClient(token=bot_token)


def clean_datetime_text(text):
    text = text.replace('：', ':')
    text = text.translate(str.maketrans('１２３４５６７８９０', '1234567890'))
    text = re.sub(r'（[月火水木金土日]）', '', text)
    text = re.sub(r'\([月火水木金土日]\)', '', text)
    text = re.sub(r'[月火水木金土日]曜日', '', text)
    text = re.sub(r'[ぁ-ん]', '', text)
    # 「月/日」または「月月日日」の形式にマッチさせる
    m = re.search(r'(\d{1,2})[月/](\d{1,2})日?', text)
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

def extract_datetime(text: str):
    cleaned_text = clean_datetime_text(text)
    now = datetime.now(JST)
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
        # naiveなdatetimeオブジェクトを作成
        naive_dt = datetime(year, month, day, hour, minute)
        dt = JST.localize(naive_dt)
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


def extract_datetime_options(text):
    options = {}
    lines = text.split('\n')
    for line in lines:
        match = re.search(r':(\w+):\s*(?:[:：]\s*)?(.+)', line)
        if match:
            emoji, datetime_str = match.groups()
            normalized_emoji = normalize_emoji(emoji)
            cleaned_str = clean_datetime_text(datetime_str)
            dt = extract_datetime(cleaned_str)
            if dt:
                options[f":{normalized_emoji}:"] = dt
                logger.info(f"日時を抽出しました: {datetime_str} -> {dt}")
            else:
                logger.info(f"日時を解析できませんでした: {datetime_str}")
    return options

def send_reminder(main_message_ts: str):
    """
    指定されたスケジュールID（main_message_ts）に基づいてリマインドを送信する
    """
    logger.info(f"リマインドジョブを実行します: {main_message_ts}")
    try:
        # DBからスケジュール情報を取得
        response = supabase.table('schedules').select('*').eq('main_message_ts', main_message_ts).single().execute()
        schedule_data = response.data

        if not schedule_data:
            logger.error(f"リマインド対象のスケジュールが見つかりません: {main_message_ts}")
            return
        
        # 参加者リストを取得
        participants = schedule_data.get('participants', {})
        selected_emoji = schedule_data.get('selected_emoji')

        if not selected_emoji or selected_emoji not in participants:
            logger.error(f"参加者情報が見つかりません: {main_message_ts}")
            return
        
        user_ids = participants[selected_emoji]

        # 1. DBからISO形式の文字列を取得
        iso_string = schedule_data['selected_datetime']
        
        # 2. タイムゾーン情報を持ったdatetimeオブジェクトに変換
        aware_dt = datetime.fromisoformat(iso_string)
        
        # 3. JSTに変換してからフォーマット
        if aware_dt.tzinfo is None:
            # タイムゾーン情報がない場合はJSTとして扱う
            jst_dt = JST.localize(aware_dt)
        else:
            # タイムゾーン情報がある場合はJSTに変換
            jst_dt = aware_dt.astimezone(JST)

        message = (
            f"🔔 リマインダーです！\n\n"
            f"明日 **{jst_dt.strftime('%m月%d日 %H:%M')}** からの予定を忘れないでね！"
        )

        # 各参加者にDMを送信
        for user_id in user_ids:
            slack_client.chat_postMessage(channel=user_id, text=message)
            logger.info(f"{user_id} にリマインドを送信しました。")
            
        # 送信済みフラグを更新
        supabase.table('schedules').update({'is_reminder_sent': True}).eq('main_message_ts', main_message_ts).execute()

    except Exception as e:
        logger.error(f"リマインド送信中にエラーが発生しました: {e}")

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
                    response = supabase.table('schedules').select('options').eq('main_message_ts', thread_ts).single().execute()
                    candidate = response.data 
                    if candidate and 'options' in candidate:
                        # DBから取得したoptionsは文字列なのでdatetimeオブジェクトに変換する必要がある
                        # ただし、この時点では文字列のままで比較しても問題ない
                        # 実際のdtオブジェクトは、決定ロジックの中で別途取得・生成する
                        decided = []
                        for emoji in emoji_matches:
                            normalized_emoji = emoji
                            m = re.match(r":(\w+):", emoji)
                            if m:
                                normalized_emoji = f":{normalize_emoji(m.group(1))}:"
                            # DBから取得した日時文字列
                        dt_str_from_db = candidate["options"].get(normalized_emoji)
                        
                        if dt_str_from_db:
                            # 文字列をdatetimeオブジェクトに変換
                            dt_obj = datetime.fromisoformat(dt_str_from_db)
                            
                            # datetimeオブジェクトを画面表示用の文字列にフォーマット
                            dt_str_for_display = dt_obj.strftime('%Y/%m/%d %H:%M')
                            
                            # decidedリストには、datetimeオブジェクトを格納する
                            decided.append((normalized_emoji, dt_obj, dt_str_for_display))
                        if decided:
                            if len(decided) == 1:
                                emoji, dt_obj, dt_str = decided[0]
                                reminder_dt = dt_obj - timedelta(days=1)
                                reminder_str = reminder_dt.strftime('%Y/%m/%d %H:%M')
                                msg = (f"日時を\n{emoji} {dt_str}\nに決定しました。\n"
                                       f"予定の24時間前（{reminder_str}頃）にリマインドします。")
                            else:
                                msg = "日時を以下で決定しました。\n"
                                for emoji, dt_obj, dt_str in decided:
                                    reminder_dt = dt_obj - timedelta(days=1)
                                    reminder_str = reminder_dt.strftime('%Y/%m/%d %H:%M')
                                    msg += f"・ {emoji} {dt_str} (リマインド: {reminder_str}頃)\n"
                            slack_client.chat_postMessage(
                                channel=channel,
                                text=msg,
                                thread_ts=thread_ts
                            )

                            for emoji, dt_obj, dt_str in decided:
                                reminder_dt = dt_obj - timedelta(days=1)

                                # 過去の日時になっていないかチェック
                                if reminder_dt > datetime.now(JST):
                                    job_id = f"reminder_{thread_ts}_{emoji.strip(':')}"
                                    job = scheduler.add_job(
                                        send_reminder,
                                        trigger='date',
                                        run_date=reminder_dt,
                                        args=[thread_ts],
                                        id=job_id,
                                        replace_existing=True # 同じIDのジョブがあれば上書き
                                    )
                                    logger.info(f"リマインドを予約しました: JobID={job.id}, Time={reminder_dt}")

                                    # ▼▼▼ デバッグ用のログを追加 ▼▼▼
                                    logger.info("--- 現在の予約済みジョブ一覧 ---")
                                    scheduler.print_jobs()
                                    logger.info("---------------------------------")
                                    # ▲▲▲ ここまで追加 ▲▲▲

                                    # DBにジョブIDなどを保存
                                    supabase.table('schedules').update({
                                        'selected_emoji': emoji,
                                        'selected_datetime': dt_obj.isoformat(),
                                        'reminder_job_id': job.id
                                    }).eq('main_message_ts', thread_ts).execute()
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
                # 新しい候補日投稿をSupabaseに保存
                save_new_schedule(message_ts, channel, options)
                logger.info(f"候補日投稿を記録しました: {message_ts}")
                logger.info(f"候補日時: {options}")
                message = f"<@{user}> 候補日を記録しました！\n```"
                for emoji, dt in options.items():
                    message += f"{emoji}: {dt.strftime('%Y/%m/%d %H:%M')}\n"
                message += "```"
                slack_client.chat_postEphemeral(channel=channel, user=user, text=message)
            else:
                logger.info(f"候補日時が見つかりませんでした: {text}")

        # リアクションが追加された場合の処理
        elif event.get("type") == "reaction_added":
            reaction = event.get("reaction", "")
            user_id = event.get("user", "")
            item = event.get("item", {})
            message_ts = item.get("ts", "")
            # データベースに該当のスケジュールが存在するか確認
            schedule_response = supabase.table('schedules').select('options').eq('main_message_ts', message_ts).single().execute()
            
            if schedule_response.data:
                schedule_options = schedule_response.data.get('options', {})
                normalized_reaction = f":{normalize_emoji(reaction)}:"

                if normalized_reaction in schedule_options:
                    # DBを更新する関数を呼び出す
                    update_participants_in_db(message_ts, normalized_reaction, user_id)
                else:
                    logger.info(f"スケジュールにないスタンプへのリアクションは無視します: {normalized_reaction}")
            else:
                logger.info(f"候補日投稿以外のメッセージに対するリアクションは無視します: {message_ts}")
                

        # リアクションが削除された場合の処理
        elif event.get("type") == "reaction_removed":
            reaction = event.get("reaction", "")
            user_id = event.get("user", "")
            item = event.get("item", {})
            message_ts = item.get("ts", "")
            
            # データベースに該当のスケジュールが存在するか確認
            schedule_response = supabase.table('schedules').select('options').eq('main_message_ts', message_ts).single().execute()
            
            if schedule_response.data:
                normalized_reaction = f":{normalize_emoji(reaction)}:"
                # DBから参加者を削除する関数を呼び出す
                remove_participant_from_db(message_ts, normalized_reaction, user_id)

    return {"ok": True}

def save_new_schedule(message_ts: str, channel_id: str, options: dict):
    """
    新しい候補日投稿をSupabaseのschedulesテーブルに保存する
    """
    try:
        options_for_db = {key: dt.isoformat() for key, dt in options.items()}
        # Supabaseに挿入するデータを作成
        insert_data = {
            "main_message_ts": message_ts,
            "channel_id": channel_id,
            "options": options_for_db,
            "participants": {}  # participantsは空のJSONで初期化
        }
        # データの挿入を実行
        supabase.table('schedules').insert(insert_data).execute()
        logger.info(f"✅ Supabaseへのスケジュール保存に成功しました。ts: {message_ts}")
    except Exception as e:
        logger.error(f"❌ Supabaseへのスケジュール保存に失敗しました。ts: {message_ts}, Error: {e}")

def update_participants_in_db(message_ts: str, emoji: str, user_id: str):
    """
    指定されたスタンプの参加者リストにユーザーを追加/更新する
    """
    try:
        # DBから現在の参加者リストを取得
        response = supabase.table('schedules').select('participants').eq('main_message_ts', message_ts).single().execute()
        
        if not response.data:
            logger.info(f"DBに該当するスケジュールがありません: {message_ts}")
            return

        participants = response.data.get('participants', {})

        # 参加者リストを更新
        if emoji not in participants:
            participants[emoji] = []
        
        # 既に参加者でなければ追加する
        if user_id not in participants[emoji]:
            participants[emoji].append(user_id)
            logger.info(f"参加者を追加: {user_id} -> {emoji}")
        else:
            logger.info(f"参加者は既に追加済みです: {user_id} -> {emoji}")
            return # 更新不要なのでここで処理を終了

        # 更新した参加者リストをDBに保存
        supabase.table('schedules').update({'participants': participants}).eq('main_message_ts', message_ts).execute()
        logger.info(f"✅ DBの参加者情報を更新しました: ts={message_ts}")

    except Exception as e:
        logger.error(f"❌ DBの参加者情報更新に失敗しました: {e}")

def remove_participant_from_db(message_ts: str, emoji: str, user_id: str):
    """
    指定されたスタンプの参加者リストからユーザーを削除する
    """
    try:
        # DBから現在の参加者リストを取得
        response = supabase.table('schedules').select('participants').eq('main_message_ts', message_ts).single().execute()
        
        if not response.data or not response.data.get('participants'):
            logger.info(f"参加者削除対象のスケジュールまたは参加者リストが見つかりません: {message_ts}")
            return

        participants = response.data.get('participants')

        # 参加者リストを更新
        if emoji in participants and user_id in participants[emoji]:
            participants[emoji].remove(user_id)
            # もしそのスタンプの参加者が誰もいなくなったら、キーごと削除する
            if not participants[emoji]:
                del participants[emoji]
            logger.info(f"参加者を削除: {user_id} -> {emoji}")
        else:
            logger.info(f"削除対象の参加者が見つかりません: {user_id} -> {emoji}")
            return # 更新不要

        # 更新した参加者リストをDBに保存
        supabase.table('schedules').update({'participants': participants}).eq('main_message_ts', message_ts).execute()
        logger.info(f"✅ DBの参加者情報を更新（削除）しました: ts={message_ts}")

    except Exception as e:
        logger.error(f"❌ DBの参加者情報更新（削除）に失敗しました: {e}")