from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    openai_api_key: str = "sk-xxx"
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o"
    max_tokens: int = 4096
    temperature: float = 0.1

    db_path: str = "review_checkpoints.db"


settings = Settings()
