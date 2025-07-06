from apscheduler.schedulers.background import BackgroundScheduler
import pytz

# タイムゾーンを'Asia/Tokyo'に設定してスケジューラを作成
scheduler = BackgroundScheduler(timezone=pytz.timezone('Asia/Tokyo'))