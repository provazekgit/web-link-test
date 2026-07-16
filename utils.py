# utils.py
import os
from functools import wraps
from flask import request, Response
from dotenv import load_dotenv

# Načti .env ze stejné složky jako proces (typicky root projektu)
load_dotenv()

# Načti přihlašovací údaje (fallbacky pro test)
BASIC_USER = os.getenv("BASIC_USER", "adminkk")
BASIC_PASS = os.getenv("BASIC_PASS", "Super-GI1983")

# Volitelně: vypni auth pro lokální vývoj přes .env:
# AUTH_OFF=1
AUTH_OFF = os.getenv("AUTH_OFF", "0") == "1"

# Debug výpis (zapni jen když chceš) přes .env:
# AUTH_DEBUG=1
if os.getenv("AUTH_DEBUG", "0") == "1":
    print("BASIC_USER =", BASIC_USER)
    print("BASIC_PASS =", BASIC_PASS)

def _check(u: str, p: str) -> bool:
    return u == BASIC_USER and p == BASIC_PASS

def _need_auth() -> Response:
    # Změněný realm: donutí prohlížeč znovu vyžádat údaje,
    # když měl uložené špatné přihlášení.
    return Response(
        "Authentication required.",
        401,
        {"WWW-Authenticate": 'Basic realm="KK Link Test"'}
    )

def requires_auth(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        # když chceš lokálně vypnout auth
        if AUTH_OFF:
            return fn(*args, **kwargs)

        auth = request.authorization
        if not auth or not _check(auth.username, auth.password):
            return _need_auth()
        return fn(*args, **kwargs)
    return wrapper

def fmt_ms(ms: int, sep: str = " ") -> str:
    """
    Formátuje číslo milisekund na přehledný řetězec.
    Např. 12345 -> '12 345'
    """
    try:
        s = f"{int(ms):,}".replace(",", sep)
    except Exception:
        s = str(ms)
    return s
