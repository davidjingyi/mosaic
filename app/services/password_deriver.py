"""
密码派生服务 — 基于 HMAC-SHA256 的确定性密码派生。

用户注册密码 → HMAC(主密钥, 密码) → 付费激活码
- 1:1 映射：同一密码永远生成同一激活码
- 单向：从激活码无法反推原始密码
- 主密钥泄露前，激活码无法伪造
"""

import hashlib
import hmac
import logging
import secrets
from base64 import urlsafe_b64encode

logger = logging.getLogger(__name__)

# 默认主密钥 — 生产环境应通过环境变量 ONCO_DERIVE_KEY 覆盖
_DEFAULT_KEY = "oncorag-derive-key-change-in-production-2026"


class PasswordDeriver:
    """密码派生器：用户密码 → 付费激活码。"""

    def __init__(self, master_key: str | None = None):
        self._key = (master_key or _DEFAULT_KEY).encode("utf-8")

    def derive(self, password: str) -> str:
        """
        从用户原始密码派生付费激活码。

        Args:
            password: 用户注册时的原始密码

        Returns:
            16位激活码，格式: XXXX-XXXX-XXXX-XXXX
        """
        if not password:
            raise ValueError("密码不能为空")

        digest = hmac.new(self._key, password.encode("utf-8"), hashlib.sha256).digest()

        # 用 urlsafe base64 编码，取前 16 字符作为激活码
        encoded = urlsafe_b64encode(digest).decode("ascii")
        code = encoded[:16].upper().replace("-", "X").replace("_", "Y")

        # 格式化为 XXXX-XXXX-XXXX-XXXX
        return f"{code[0:4]}-{code[4:8]}-{code[8:12]}-{code[12:16]}"

    def verify(self, password: str, code: str) -> bool:
        """
        验证激活码是否匹配用户密码。

        Args:
            password: 用户原始密码
            code: 待验证的激活码

        Returns:
            True 如果激活码由该密码派生
        """
        return hmac.compare_digest(self.derive(password), code.upper())


# 全局单例
_deriver: PasswordDeriver | None = None


def get_deriver(master_key: str | None = None) -> PasswordDeriver:
    """获取全局密码派生器实例。"""
    global _deriver
    if _deriver is None:
        from app.config import Settings
        from pathlib import Path
        try:
            settings = Settings.from_yaml(Path("data/config.yaml"))
            key = settings.derive_master_key
        except Exception:
            key = None
            
        if not key:
            key = secrets.token_hex(32)
            logger.warning(
                "derive_master_key 未设置，使用随机密钥。"
                "重启后所有已发放激活码将失效！"
            )
        _deriver = PasswordDeriver(master_key or key)
    return _deriver


def derive_activation_code(password: str) -> str:
    """快捷方法：派生激活码。"""
    return get_deriver().derive(password)


def verify_activation_code(password: str, code: str) -> bool:
    """快捷方法：验证激活码。"""
    return get_deriver().verify(password, code)
