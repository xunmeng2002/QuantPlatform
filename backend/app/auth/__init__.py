"""auth: 口令散列、令牌签发与当前用户依赖.

不引入第三方 JWT 库, 用标准库 hmac + hashlib + base64 实现 HS256, 口令用 PBKDF2-HMAC-SHA256.
"""
