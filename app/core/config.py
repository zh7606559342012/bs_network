import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
import yaml
from loguru import logger

class AppConfig(BaseSettings):
    addr: str = "0.0.0.0"
    port: str = "20000"
    version: str = "1.0.0"
    nfip: str = "127.0.0.1"
    telnet_addr: str = ":20055"


class LogConfig(BaseSettings):
    log_path: str = "/var/log/monitor_agent/"
    log_level: str = "INFO"


class RedisConfig(BaseSettings):
    addr: str = "127.0.0.1"
    port: int = 6379
    password: str = "Jxtx2024@88"


class OmsConfig(BaseSettings):
    oms_proto: str = "https"
    oms_port: str = ""
    oms_ip: str = ""
    oms_k8s: bool = False


class Settings(BaseSettings):
    app: AppConfig = Field(default_factory=AppConfig)
    log: LogConfig = Field(default_factory=LogConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    oms: OmsConfig = Field(default_factory=OmsConfig)

    model_config = SettingsConfigDict(env_file=".env", env_prefix="APP_")

    @classmethod
    def load_from_yaml(cls, yaml_path: str = "conf/agent.yaml"):
        """加载 YAML 配置（兼容原 Go 项目）"""
        if not os.path.exists(yaml_path):
            logger.warning(f"Config file {yaml_path} not found, using defaults")
            return cls()

        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        settings = cls()
        if data:
            settings.app = AppConfig(**data.get("app", {}))
            settings.log = LogConfig(**data.get("log", {}))
            if "db" in data and "redis" in data["db"]:
                settings.redis = RedisConfig(**data["db"]["redis"])
            settings.oms = OmsConfig(**data.get("oms", {}))
        return settings


# 全局配置实例
settings = Settings.load_from_yaml()