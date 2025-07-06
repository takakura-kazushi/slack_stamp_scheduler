import os
from supabase import create_client, Client
from dotenv import load_dotenv

# .envファイルを読み込む
load_dotenv()

url: str = os.getenv("SUPABASE_URL")
key: str = os.getenv("SUPABASE_KEY")

# Supabaseクライアントを初期化
supabase: Client = create_client(url, key)