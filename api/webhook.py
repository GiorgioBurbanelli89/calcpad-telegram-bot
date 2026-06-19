"""
Bot de Calcpad en formato WEBHOOK (serverless, para Vercel).

Telegram EMPUJA cada update a esta función (POST). La función traduce/modera
y responde llamando a la Bot API. No hay proceso 24/7: corre solo cuando llega
un mensaje -> cabe en el plan gratis de Vercel (sin tarjeta).

Variables de entorno (Vercel -> Settings -> Environment Variables):
  BOT_TOKEN        token de @BotFather (obligatorio)
  MM_EMAIL         email para subir la cuota de MyMemory (opcional)
  WEBHOOK_SECRET   token secreto que valida que el POST viene de Telegram (opcional)
  BAN_CRYPTO       "1" (por defecto) banea cripto/porno; "0" solo borra
  MOD_CHATS        ids de grupos a moderar, separados por coma
"""
import os
import re
import json
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler

from langdetect import detect, DetectorFactory

DetectorFactory.seed = 0

TOKEN = os.environ.get("BOT_TOKEN", "")
MM_EMAIL = os.environ.get("MM_EMAIL", "")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
BAN_CRYPTO = os.environ.get("BAN_CRYPTO", "1") == "1"
MOD_CHATS = {int(x) for x in os.environ.get("MOD_CHATS", "-1002195732409").split(",")
             if x.strip().lstrip("-").isdigit()}

API = "https://api.telegram.org/bot" + TOKEN + "/"

# ---------- Bot API (síncrono, urllib: sin dependencias extra) ----------
def tg(method, **params):
    data = urllib.parse.urlencode(
        {k: v for k, v in params.items() if v is not None}).encode("utf-8")
    try:
        with urllib.request.urlopen(API + method, data=data, timeout=15) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return {}


def http_get(url, params):
    q = urllib.parse.urlencode(params)
    req = urllib.request.Request(url + "?" + q, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read().decode("utf-8"), r.status


# ---------- traducción (Google free + respaldo MyMemory) ----------
IDIOMAS = {"es": "🇪🇸 ES", "en": "🇬🇧 EN"}
GLOSARIO = ["Calcpad", "ETABS", "SAP2000", "Octave", "awatif", "Hekatan", "Mxy",
            "Python", "Saint-Venant", "Timoshenko", "Kirchhoff"]


def _prot(t):
    r = {}
    for i, term in enumerate(GLOSARIO):
        p = re.compile(re.escape(term), re.IGNORECASE)
        if p.search(t):
            t = p.sub("XX%dXX" % i, t)
            r["XX%dXX" % i] = term
    return t, r


def _rest(t, r):
    for m, term in r.items():
        t = t.replace(m, term)
    return t


def _g_one(text, source, target):
    # 1) Google free (mejor calidad). Desde datacenter a veces da 429 -> respaldo.
    try:
        params = {"client": "gtx", "sl": source, "tl": target, "dt": "t", "q": text}
        body, status = http_get("https://translate.googleapis.com/translate_a/single", params)
        if status == 200:
            data = json.loads(body)
            if data and data[0]:
                out = "".join(seg[0] for seg in data[0] if seg and seg[0])
                if out.strip():
                    return out
    except Exception:
        pass
    # 2) Respaldo: MyMemory (funciona desde servidores).
    try:
        params = {"q": text, "langpair": "%s|%s" % (source, target)}
        if MM_EMAIL:
            params["de"] = MM_EMAIL
        body, status = http_get("https://api.mymemory.translated.net/get", params)
        d = json.loads(body)
        if str(d.get("responseStatus")) == "200":
            return (d.get("responseData", {}) or {}).get("translatedText", "") or ""
    except Exception:
        pass
    return ""


# ES<->EN: heurística de respaldo cuando langdetect duda (textos cortos).
_ES_MARK = set("áéíóúñÁÉÍÓÚÑ¿¡")
_ES_WORDS = {"de", "la", "el", "que", "y", "en", "un", "una", "los", "las", "del",
             "por", "para", "con", "se", "es", "no", "como", "más", "pero", "este",
             "esta", "esto", "si", "al", "lo", "su", "mi", "muy", "ya", "hay", "ser",
             "fue", "hola", "gracias", "buenos", "días", "qué", "cómo", "cuándo"}
_EN_WORDS = {"the", "is", "a", "of", "and", "to", "in", "for", "with", "on", "this",
             "that", "it", "be", "are", "was", "at", "by", "an", "as", "or", "from",
             "you", "we", "they", "load", "beam", "stress", "hello", "thanks",
             "please", "how", "what", "when", "good", "morning"}


def _decide_lang(t):
    low = t.lower()
    toks = re.findall(r"[a-záéíóúñ]+", low)
    es = sum(1 for w in toks if w in _ES_WORDS) + sum(1 for ch in t if ch in _ES_MARK)
    en = sum(1 for w in toks if w in _EN_WORDS)
    if es > en:
        return "es"
    if en > es:
        return "en"
    return None


def traducir(texto):
    limpio = texto.strip()
    real = re.sub(r"[^\wÁÉÍÓÚáéíóúÑñ]", "", limpio, flags=re.UNICODE)
    # No traducir palabras sueltas ni textos muy cortos (detección no fiable).
    if len(limpio.split()) < 2 or len(real) < 8:
        return None
    try:
        origen = detect(limpio)
    except Exception:
        origen = None
    if origen not in ("es", "en"):
        origen = _decide_lang(limpio)
        if origen is None:
            return None
    destino = "en" if origen == "es" else "es"
    prot, rr = _prot(limpio)
    out = _rest(_g_one(prot, origen, destino), rr).strip()
    if out and out.lower() != limpio.lower():
        return "%s: %s" % (IDIOMAS.get(destino, destino.upper()), out)
    return None


# ---------- moderación ----------
CRIPTO = ["bitcoin", "btc", "ethereum", "usdt", "tether", "binance", "kucoin", "coinbase",
          "criptomoneda", "cripto", "crypto", "airdrop", "metamask", "nft", "forex",
          "señales de trading", "trading signals", "inversión garantizada", "ganar dinero",
          "dinero fácil", "earn money", "double your", "free bitcoin", "rendimiento diario"]
PORNO = ["porn", "porno", "xxx", "onlyfans", "escort", "hot girls", "viagra", "casino",
         "apuestas deportivas", "bet365", "1xbet", "nude", "sexo", "sexting", "desnudo", "desnuda"]
PRESION = [
    "para permanecer en el grupo", "para seguir en el grupo",
    "debes aportar al menos", "tienes que aportar al menos", "aporta o serás",
    "aporta o seras", "aportar o salir", "si no aportas serás", "si no aportas seras",
    "serás expulsado", "seras expulsado", "serás eliminado del grupo",
    "te expulsaré del grupo", "te expulsare del grupo",
    "te saco del grupo", "te echo del grupo", "te elimino del grupo",
    "must contribute at least", "to remain in the group", "to stay in this group",
    "or you will be removed", "or you'll be removed", "or you will be kicked",
    "looking for new partners", "new partners for online collaboration",
    "online collaboration", "send us a private message",
    "trabaja desde casa", "gana desde casa", "work from home and earn",
]


def _rx(words):
    return re.compile(r"\b(" + "|".join(re.escape(w) for w in words) + r")\b", re.IGNORECASE)


CRIPTO_RE = _rx(CRIPTO)
PORNO_RE = _rx(PORNO)
PRESION_RE = re.compile("|".join(re.escape(p) for p in PRESION), re.IGNORECASE)
LINK_RE = re.compile(r"(https?://|www\.|t\.me/|@\w+bot\b|\b[\w-]+\.(com|net|org|io|ru|xyz|info|live|app|me)\b)", re.I)
WHITELIST = ["github.com", "youtube.com", "youtu.be", "calcpad", "wikipedia.org",
             "t.me/calcgrupo", "scholar.google", "researchgate", "stackoverflow.com",
             "/octave2024", "anaconda", "python.org", "numpy.org", "scipy.org"]


def es_admin(chat_id, user_id):
    r = tg("getChatAdministrators", chat_id=chat_id)
    if not r.get("ok"):
        return False
    return user_id in {a["user"]["id"] for a in r.get("result", [])}


def moderar(msg):
    """Devuelve True si borró el mensaje (entonces no se traduce)."""
    chat = msg.get("chat", {})
    chat_id = chat.get("id")
    if chat.get("type") == "private":
        return False
    if MOD_CHATS and chat_id not in MOD_CHATS:
        return False
    user = msg.get("from", {})
    uid = user.get("id")
    if not uid or es_admin(chat_id, uid):
        return False
    texto = msg.get("text") or msg.get("caption") or ""
    low = " " + texto.lower() + " "
    mid = msg.get("message_id")

    motivo = None
    if CRIPTO_RE.search(texto):
        motivo = "cripto"
    elif PORNO_RE.search(texto):
        motivo = "obsceno"
    if motivo:
        tg("deleteMessage", chat_id=chat_id, message_id=mid)
        if BAN_CRYPTO:
            tg("banChatMember", chat_id=chat_id, user_id=uid)
        return True

    if PRESION_RE.search(texto):
        tg("deleteMessage", chat_id=chat_id, message_id=mid)
        return True

    if LINK_RE.search(texto) and not any(d in low for d in WHITELIST):
        tg("deleteMessage", chat_id=chat_id, message_id=mid)
        nombre = user.get("first_name", "Usuario")
        tg("sendMessage", chat_id=chat_id,
           text="⚠️ %s: solo se permiten links de ingeniería/programación." % nombre)
        return True
    return False


# ---------- manejo del update ----------
def manejar_update(update):
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return
    if msg.get("via_bot") or (msg.get("from", {}) or {}).get("is_bot"):
        return
    if msg.get("chat", {}).get("type") != "private":
        if moderar(msg):
            return
    texto = msg.get("text")
    if not texto:
        return
    salida = traducir(texto)
    if salida:
        tg("sendMessage", chat_id=msg["chat"]["id"], text=salida,
           reply_to_message_id=msg.get("message_id"))


# ---------- handler HTTP de Vercel ----------
class handler(BaseHTTPRequestHandler):
    def _ok(self, body="ok"):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def do_GET(self):
        # Para probar en el navegador que la función vive.
        self._ok("Calcpad webhook activo")

    def do_POST(self):
        # Valida el secreto (si está configurado).
        if WEBHOOK_SECRET:
            recv = self.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
            if recv != WEBHOOK_SECRET:
                self.send_response(401)
                self.end_headers()
                return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            update = json.loads(raw.decode("utf-8"))
            manejar_update(update)
        except Exception:
            pass
        # Siempre 200 para que Telegram no reintente en bucle.
        self._ok()
