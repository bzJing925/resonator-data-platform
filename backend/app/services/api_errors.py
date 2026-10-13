"""Structured error detail used by the new platform endpoints."""
from fastapi import HTTPException


def api_error(status, code, message, *, retryable=False, context=None):
    return HTTPException(status, {'code': code, 'message': message,
                                 'retryable': retryable, 'context': context or {}})
