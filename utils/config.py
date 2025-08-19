 class Settings:
     ENV: str
     VERIFY_TOKEN: str
     WHATSAPP_TOKEN: str
     PHONE_NUMBER_ID: str
     GRAPH_BASE_URL: str
     OPENAI_API_KEY: str
     OPENAI_MODEL: str
+    OPENAI_EMBED_MODEL: str
     AUTORAG_BASE_URL: str
     AUTORAG_ADMIN_TOKEN: str
     ADMIN_TOKEN: str
+    MEMORY_DB_PATH: str

     @staticmethod
     def from_env():
         return Settings(
             ENV=os.getenv("ENV", "dev"),
@@
             OPENAI_API_KEY=os.getenv("OPENAI_API_KEY", ""),
             OPENAI_MODEL=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
+            OPENAI_EMBED_MODEL=os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small"),
             AUTORAG_BASE_URL=os.getenv("AUTORAG_BASE_URL", "http://autorag:8000"),
             AUTORAG_ADMIN_TOKEN=os.getenv("AUTORAG_ADMIN_TOKEN", ""),
             ADMIN_TOKEN=os.getenv("ADMIN_TOKEN", "adm_123"),
+            MEMORY_DB_PATH=os.getenv("MEMORY_DB_PATH", "./memory.db"),
         )
