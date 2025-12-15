"""K-Hunter Trading System - Custom Exceptions"""


class KHunterException(Exception):
    """Base exception for K-Hunter system"""
    pass


class KISAPIException(KHunterException):
    """KIS API related exceptions"""

    def __init__(self, message: str, error_code: str = None, response: dict = None):
        self.error_code = error_code
        self.response = response
        super().__init__(f"[{error_code}] {message}" if error_code else message)


class KISAuthenticationError(KISAPIException):
    """Authentication/Token related errors"""
    pass


class KISOrderError(KISAPIException):
    """Order execution errors"""
    pass


class KISRateLimitError(KISAPIException):
    """Rate limit exceeded"""
    pass


class KISConnectionError(KISAPIException):
    """Connection/Network errors"""
    pass


class ConfigurationError(KHunterException):
    """Configuration related errors"""
    pass


class InsufficientFundsError(KHunterException):
    """Insufficient funds for order"""
    pass


class InvalidOrderError(KHunterException):
    """Invalid order parameters"""
    pass
