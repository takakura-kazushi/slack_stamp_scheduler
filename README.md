# slack_stamp_scheduler

Slack のリアクション(スタンプ)を用いて日程調整を簡単に行い、日程決定後は従ってリマインドを送信する Slack ボット。

## 起動手順

### FastAPI サーバーの起動

app で以下を実行

```
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### 一時的な公開 URL の生成

```
ngrok http 8000
```

### Slack API に RequestURL を登録

Event Subscription で ngrok によって生成された URL を登録(末尾に slack/events をつける)
