from fastapi import FastAPI
from contextlib import asynccontextmanager
import logging
from dotenv import load_dotenv
from .scheduler import scheduler

# .envファイルの読み込みとロギング設定
load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    アプリケーションの起動時と終了時に処理を実行するライフスパンマネージャー
    """
    logger.info("アプリケーションの起動を開始します...")
    # アプリケーション起動時にスケジューラを開始
    scheduler.start()
    logger.info("スケジューラを開始しました。")
    yield
    # アプリケーション終了時にスケジューラを停止
    logger.info("アプリケーションのシャットダウンを開始します...")
    scheduler.shutdown()
    logger.info("スケジューラを停止しました。")

app = FastAPI(lifespan=lifespan)

# Slack Bot 用の処理は slack_events.py に切り出し
from .slack_events import router as slack_router
app.include_router(slack_router)

logger.info("FastAPIアプリケーションの設定が完了しました。")