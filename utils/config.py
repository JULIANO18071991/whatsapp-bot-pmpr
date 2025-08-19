import os
from dataclasses import dataclass

@dataclass
class Settings:
    ENV: str
    VERIFY_TOKEN: str
    WHATSAPP_TOKEN: str
    PHONE_NUMBER_ID: str
    GRAPH_BASE_URL: str
    OPENAI_API_KEY: str
    OPENAI_MODEL: str
    AUTORAG_BASE_URL: str
    AUTORAG_ADMIN_TOKEN: str
    ADMIN_TOKEN: str

    @staticmethod
    def from_env():
        return Settings(
            ENV=os.getenv("ENV", "dev"),
            VERIFY_TOKEN=os.getenv("VERIFY_TOKEN", "verify_123"),
            WHATSAPP_TOKEN=os.getenv("WHATSAPP_TOKEN", ""),
            PHONE_NUMBER_ID=os.getenv("PHONE_NUMBER_ID", ""),
            GRAPH_BASE_URL=os.getenv("GRAPH_BASE_URL", "https://graph.facebook.com/v20.0"),
            OPENAI_API_KEY=os.getenv("OPENAI_API_KEY", ""),
            OPENAI_MODEL=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            AUTORAG_BASE_URL=os.getenv("AUTORAG_BASE_URL", "http://autorag:8000"),
            AUTORAG_ADMIN_TOKEN=os.getenv("AUTORAG_ADMIN_TOKEN", ""),
            ADMIN_TOKEN=os.getenv("ADMIN_TOKEN", "adm_123"),
        )
