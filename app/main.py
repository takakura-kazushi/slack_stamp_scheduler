from fastapi import FastAPI
from slack_sdk.web import WebClient
from dotenv import load_dotenv
import os, logging

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# Slack Bot 用の処理は slack_events.py に切り出し
from .slack_events import router as slack_router
app.include_router(slack_router)