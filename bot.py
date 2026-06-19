"""
Bot de Calcpad: TRADUCTOR (ES<->EN multi-idioma) + MODERADOR para tus grupos.
Motor traducción: MyMemory (gratis, sin API key). Corre en tu PC (o servidor).

MODERACIÓN (solo a NO administradores):
  - Cripto/Bitcoin/estafas  -> borra el mensaje + BANEA al autor (son bots spammers).
  - Pornografía / obsceno   -> borra + BANEA.
  - Presión/condicionamiento/amenaza ("aporta o te saco", "buscamos socios") -> borra (no banea).
  - Links NO permitidos      -> borra + avisa (1ra vez); reincidencia -> banea.
  - Registra TODO en moderacion.log (quién, qué, cuándo).
  - Mensaje de bienvenida con las reglas a cada nuevo miembro.
  - Los administradores y el dueño NUNCA son moderados.

Config por entorno:
  BOT_TOKEN, TARGET_LANGS ("es,en"), MM_EMAIL (opcional)
  BAN_CRYPTO=1  (1=banea cripto/porno, 0=solo borra)

Requisitos del bot en el grupo: ADMIN con permisos "Eliminar mensajes" y "Expulsar usuarios".
"""

import os
import re
import time
import asyncio
import logging
import socket

# Forzar IPv4: en algunos hosts (p.ej. Hugging Face Spaces) el egress IPv6
# está roto y la conexión a api.telegram.org da ConnectTimeout. Filtramos las
# resoluciones DNS para que solo devuelvan direcciones IPv4.
_orig_getaddrinfo = socket.getaddrinfo
def _getaddrinfo_ipv4(*args, **kwargs):
    res = _orig_getaddrinfo(*args, **kwargs)
    ipv4 = [r for r in res if r[0] == socket.AF_INET]
    return ipv4 or res
# DESACTIVADO 2026-06-18: forzar IPv4 rompía la salida a api.telegram.org desde HF
# (get_me fallaba en el arranque). Dejamos la resolución por defecto (IPv4 + IPv6).
# socket.getaddrinfo = _getaddrinfo_ipv4

import httpx
from telegram import Update
from telegram.ext import (Application, MessageHandler, CommandHandler,
                          ChatMemberHandler, filters, ContextTypes)
from telegram.request import HTTPXRequest
from langdetect import detect, DetectorFactory

DetectorFactory.seed = 0

# En Hugging Face el token va como SECRETO (BOT_TOKEN). Nunca se escribe aquí.
TOKEN = os.environ.get("BOT_TOKEN", "")
TARGET_LANGS = [s.strip() for s in os.environ.get("TARGET_LANGS", "es,en").split(",") if s.strip()]
MM_EMAIL = os.environ.get("MM_EMAIL", "")
BAN_CRYPTO = os.environ.get("BAN_CRYPTO", "1") == "1"
# Grupos donde SÍ se modera (borrar/banear/avisar). En el resto, el bot SOLO traduce.
# Por defecto solo el grupo Calcpad. Configurable con MOD_CHATS (ids separados por coma).
MOD_CHATS = {int(x) for x in os.environ.get("MOD_CHATS", "-1002195732409").split(",")
             if x.strip().lstrip("-").isdigit()}

# ---------- logging de actividad + moderación ----------
logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger("calcpad-bot")
modlog = logging.getLogger("moderacion")
_fh = logging.FileHandler("moderacion.log", encoding="utf-8")
_fh.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
modlog.addHandler(_fh)
modlog.setLevel(logging.INFO)

# ---------- traducción ----------
IDIOMAS = {"es": "🇪🇸 ES", "en": "🇬🇧 EN", "ru": "🇷🇺 RU", "ar": "🇸🇦 AR",
           "fa": "🇮🇷 FA", "pt": "🇧🇷 PT", "fr": "🇫🇷 FR", "de": "🇩🇪 DE"}
GLOSARIO = ["Calcpad", "ETABS", "SAP2000", "Octave", "awatif", "Hekatan", "Mxy",
            "Python", "Saint-Venant", "Timoshenko", "Kirchhoff"]

# ---------- reglas de moderación ----------
CRIPTO = ["bitcoin", "btc", "ethereum", "usdt", "tether", "binance", "kucoin", "coinbase",
          "criptomoneda", "cripto", "crypto", "airdrop", "metamask", "nft", "forex",
          "señales de trading", "trading signals", "inversión garantizada", "ganar dinero",
          "dinero fácil", "earn money", "double your", "free bitcoin", "rendimiento diario"]
PORNO = ["porn", "porno", "xxx", "onlyfans", "escort", "hot girls", "viagra", "casino",
         "apuestas deportivas", "bet365", "1xbet", "nude", "sexo", "sexting", "desnudo", "desnuda"]

# Frases de PRESIÓN / CONDICIONAMIENTO / AMENAZA (estilo "aporta o te saco",
# "buscamos socios para colaboración online"). Son frases completas (no palabras
# sueltas) para evitar falsos positivos en charla normal de ingeniería.
PRESION = [
    "para permanecer en el grupo", "para seguir en el grupo",
    "debes aportar al menos", "tienes que aportar al menos", "aporta o serás",
    "aporta o seras", "aportar o salir", "si no aportas serás", "si no aportas seras",
    "serás expulsado", "seras expulsado", "serás eliminado del grupo",
    "te expulsaré del grupo", "te expulsare del grupo",
    "te saco del grupo", "te echo del grupo", "te elimino del grupo",
    "must contribute at least", "to remain in the group", "to stay in this group",
    "or you will be removed", "or you'll be removed", "or you will be kicked",
    # reclutamiento con presión (spam tipo "buscamos socios")
    "looking for new partners", "new partners for online collaboration",
    "online collaboration", "send us a private message",
    "trabaja desde casa", "gana desde casa", "work from home and earn",
]


def _rx(words):
    return re.compile(r"\b(" + "|".join(re.escape(w) for w in words) + r")\b", re.IGNORECASE)


CRIPTO_RE = _rx(CRIPTO)
PORNO_RE = _rx(PORNO)
# Sin \b: son frases con espacios; coincidencia directa, ignorando mayúsculas.
PRESION_RE = re.compile("|".join(re.escape(p) for p in PRESION), re.IGNORECASE)
LINK_RE = re.compile(r"(https?://|www\.|t\.me/|@\w+bot\b|\b[\w-]+\.(com|net|org|io|ru|xyz|info|live|app|me)\b)", re.I)
WHITELIST = ["github.com", "youtube.com", "youtu.be", "calcpad", "wikipedia.org",
             "t.me/calcgrupo", "scholar.google", "researchgate", "stackoverflow.com",
             "/octave2024", "anaconda", "python.org", "numpy.org", "scipy.org"]

_admins = {}        # chat_id -> (set(ids), timestamp)
_avisos = {}        # (chat_id, user_id) -> nº de avisos


def _prot(t):
    r = {}
    for i, term in enumerate(GLOSARIO):
        p = re.compile(re.escape(term), re.IGNORECASE)
        if p.search(t):
            t = p.sub(f"XX{i}XX", t); r[f"XX{i}XX"] = term
    return t, r


def _rest(t, r):
    for m, term in r.items():
        t = t.replace(m, term)
    return t


def _chunks(s, n=480):
    """Parte el texto en pedazos <= n caracteres, respetando palabras."""
    out, cur = [], ""
    for w in s.split(" "):
        if len(cur) + len(w) + 1 > n and cur:
            out.append(cur); cur = ""
        cur = (cur + " " + w).strip()
    if cur:
        out.append(cur)
    return out


async def _g_one(text, source, target):
    # Motor con RESPALDO. El endpoint gratis de Google (translate_a) da mejor
    # calidad PERO bloquea (HTTP 429) las IPs de datacenter como las de Hugging
    # Face → en el Space fallaba en silencio. Ahora: intentamos Google y, si
    # falla/bloquea, caemos a MyMemory (que sí anda desde servidores).
    # 1) Google free (mejor calidad)
    try:
        params = {"client": "gtx", "sl": source, "tl": target, "dt": "t", "q": text}
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get("https://translate.googleapis.com/translate_a/single",
                            params=params, headers={"User-Agent": "Mozilla/5.0"})
            if r.status_code == 200:
                data = r.json()
                if data and data[0]:
                    out = "".join(seg[0] for seg in data[0] if seg and seg[0])
                    if out.strip():
                        return out
            else:
                log.warning("google %s (IP bloqueada?) -> MyMemory", r.status_code)
    except Exception as e:
        log.warning("google trad fallo (%s) -> MyMemory", e)
    # 2) Respaldo: MyMemory API (funciona desde datacenter/HF)
    try:
        params = {"q": text, "langpair": f"{source}|{target}"}
        if MM_EMAIL:
            params["de"] = MM_EMAIL
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get("https://api.mymemory.translated.net/get", params=params)
            d = r.json()
            if d.get("responseStatus") in (200, "200"):
                return (d.get("responseData", {}) or {}).get("translatedText", "") or ""
            log.warning("mymemory status %s: %s", d.get("responseStatus"),
                        str(d.get("responseDetails"))[:120])
    except Exception as e:
        log.warning("mymemory trad fallo: %s", e)
    return ""


async def mm(text, source, target):
    # ~1200 chars por petición para no exceder el largo de la URL (GET).
    if len(text) <= 1200:
        return await _g_one(text, source, target)
    partes = _chunks(text, 1200)[:30]   # tope 30 pedazos (~36000 chars)
    res = []
    for p in partes:
        t = await _g_one(p, source, target)
        if t:
            res.append(t)
    return " ".join(res)


# El bot opera en grupos bilingües ES<->EN. Para textos cortos/ambiguos
# langdetect se equivoca (p.ej. "Traductor" -> rumano), así que restringimos
# la detección a es/en con una heurística de respaldo.
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
    """Decide es/en para texto corto/ambiguo. Devuelve 'es', 'en' o None."""
    low = t.lower()
    toks = re.findall(r"[a-záéíóúñ]+", low)
    es = sum(1 for w in toks if w in _ES_WORDS) + sum(1 for ch in t if ch in _ES_MARK)
    en = sum(1 for w in toks if w in _EN_WORDS)
    if es > en:
        return "es"
    if en > es:
        return "en"
    return None


async def traducir(texto):
    limpio = texto.strip()
    real = re.sub(r"[^\wÁÉÍÓÚáéíóúÑñ]", "", limpio, flags=re.UNICODE)
    # No traducir palabras sueltas ni textos muy cortos: la detección de idioma
    # no es fiable ahí y no aporta ("ok", "jaja", "Traductor", emojis, etc.).
    if len(limpio.split()) < 2 or len(real) < 8:
        return []
    try:
        origen = detect(limpio)
    except Exception:
        origen = None
    # Solo ES<->EN. Si detecta otro idioma (ro/ca/it por parecido con el
    # español) lo reevaluamos por heurística; si sigue sin decidirse, no traduce.
    if origen not in ("es", "en"):
        origen = _decide_lang(limpio)
        if origen is None:
            return []
    destino = "en" if origen == "es" else "es"
    prot, rr = _prot(limpio)
    try:
        out = _rest(await mm(prot, origen, destino), rr).strip()
    except Exception as e:
        log.warning("trad %s->%s: %s", origen, destino, e)
        return []
    if out and out.lower() != limpio.lower():
        return [(IDIOMAS.get(destino, destino.upper()), out)]
    return []


async def es_admin(bot, chat_id, user_id):
    ids, ts = _admins.get(chat_id, (set(), 0))
    if time.time() - ts > 300:  # refrescar cada 5 min
        try:
            admins = await bot.get_chat_administrators(chat_id)
            ids = {a.user.id for a in admins}
            _admins[chat_id] = (ids, time.time())
        except Exception:
            pass
    return user_id in ids


async def moderar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Devuelve True si borró el mensaje (entonces no se traduce)."""
    msg = update.effective_message
    if not msg or msg.chat.type == "private":
        return False
    if MOD_CHATS and msg.chat_id not in MOD_CHATS:
        return False  # grupo no listado: solo traducir, sin moderar
    user = msg.from_user
    if not user or await es_admin(context.bot, msg.chat_id, user.id):
        return False  # admins/dueño nunca se moderan
    texto = (msg.text or msg.caption or "")
    low = " " + texto.lower() + " "
    quien = f"{user.full_name} (@{user.username or user.id})"

    # 1) CRIPTO o PORNO -> borrar + banear
    motivo = None
    if CRIPTO_RE.search(texto):
        motivo = "cripto/estafa"
    elif PORNO_RE.search(texto):
        motivo = "contenido obsceno"
    if motivo:
        try:
            await msg.delete()
        except Exception:
            pass
        accion = "borrado"
        if BAN_CRYPTO:
            try:
                await context.bot.ban_chat_member(msg.chat_id, user.id)
                accion = "borrado + BANEADO"
            except Exception as e:
                log.warning("no pude banear: %s", e)
        modlog.info("[%s] %s -> %s | %r", motivo, quien, accion, texto[:120])
        return True

    # 1b) PRESIÓN / CONDICIONAMIENTO / AMENAZA -> borrar (sin banear: puede ser miembro real)
    if PRESION_RE.search(texto):
        try:
            await msg.delete()
        except Exception:
            pass
        modlog.info("[presion/condicionamiento] %s -> borrado | %r", quien, texto[:120])
        return True

    # 2) LINK no permitido -> borrar + avisar (reincidencia -> banear)
    if LINK_RE.search(texto) and not any(d in low for d in WHITELIST):
        try:
            await msg.delete()
        except Exception:
            pass
        k = (msg.chat_id, user.id)
        _avisos[k] = _avisos.get(k, 0) + 1
        if _avisos[k] >= 3:
            try:
                await context.bot.ban_chat_member(msg.chat_id, user.id)
                modlog.info("[link x3] %s -> BANEADO | %r", quien, texto[:120])
            except Exception:
                pass
        else:
            try:
                await context.bot.send_message(
                    msg.chat_id,
                    f"⚠️ {user.mention_html()}: solo se permiten links de ingeniería/programación. "
                    f"Aviso {_avisos[k]}/3.", parse_mode="HTML")
            except Exception:
                pass
            modlog.info("[link] %s -> borrado (aviso %d) | %r", quien, _avisos[k], texto[:120])
        return True
    return False


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not (msg.text or msg.caption):
        return
    if msg.via_bot or (msg.from_user and msg.from_user.is_bot):
        return
    # registrar actividad
    if msg.chat.type != "private":
        modlog.info("[msg] %s: %r", f"@{msg.from_user.username or msg.from_user.id}",
                    (msg.text or msg.caption or "")[:200])
    # moderar primero; si borró, no traducir
    if await moderar(update, context):
        return
    if not msg.text:
        return
    salidas = await traducir(msg.text)
    if salidas:
        texto = "\n".join(f"{e}: {t}" for e, t in salidas)
        # La conexión HF->Telegram para ENVIAR a veces da timeout (handshake en frío).
        # Reintentamos: el primer envío calienta el pool y los siguientes son rápidos.
        for intento in range(8):
            try:
                await msg.reply_text(texto, do_quote=True)
                break
            except Exception as e:
                log.warning("envio fallo (intento %d): %s", intento + 1, e)
                await asyncio.sleep(1.0)


async def on_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmu = update.chat_member
    if not cmu:
        return
    # El mensaje de bienvenida (que menciona Calcpad) SOLO en los grupos a moderar.
    if MOD_CHATS and cmu.chat.id not in MOD_CHATS:
        return
    if cmu.old_chat_member.status in ("left", "kicked") and cmu.new_chat_member.status == "member":
        u = cmu.new_chat_member.user
        if u.is_bot:
            return
        modlog.info("[join] %s se unió", f"{u.full_name} (@{u.username or u.id})")
        try:
            await context.bot.send_message(
                cmu.chat.id,
                f"👋 Bienvenido/a {u.mention_html()} a Calcpad.\n"
                "📚 Este grupo es un archivo de material libre, sin presión: "
                "toma lo que necesites. Para conversar, usa el tema "
                "'Charla - Calcpad y similares'.\n"
                "🚫 Prohibido: cripto/bitcoin, links ajenos al tema, contenido obsceno. "
                "El bot modera automáticamente.", parse_mode="HTML")
        except Exception:
            pass


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "Soy el bot de Calcpad: traduzco ES⇄EN y modero el grupo "
        "(borro cripto, spam y contenido obsceno). 🌐🛡️")


async def reglas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "📌 Reglas del grupo:\n"
        "1. Solo ingeniería, programación, Calcpad y programas similares.\n"
        "2. La charla va en el tema 'Charla - Calcpad y similares'; "
        "el resto de temas es archivo de consulta.\n"
        "3. Prohibido cripto/bitcoin/estafas → baneo automático.\n"
        "4. Prohibido contenido obsceno/pornografía → baneo automático.\n"
        "5. No links ajenos al tema (3 avisos → baneo).\n"
        "6. Sin presión: el material es libre, NO se exige aportar para permanecer.\n"
        "7. Traducción ES⇄EN automática para todos.")


async def _keepalive(app):
    # Mantiene CALIENTE la conexión de envío a Telegram con un ping ligero.
    # Sin esto, cada respuesta hace un "handshake en frío" que en HF se cuelga.
    while True:
        try:
            await app.bot.get_me()
        except Exception:
            pass
        await asyncio.sleep(3)


async def _post_init(app):
    asyncio.create_task(_keepalive(app))


def build_application():
    if not TOKEN or TOKEN == "PEGA_TU_TOKEN_DE_BOTFATHER":
        raise SystemExit("Falta BOT_TOKEN.")
    # Para ENVIAR: connect timeout corto + pool grande, para que los reintentos
    # ciclen rápido (el handshake en frío HF->Telegram a veces se cuelga).
    req = HTTPXRequest(connect_timeout=12.0, read_timeout=20.0,
                       write_timeout=20.0, pool_timeout=20.0,
                       connection_pool_size=8)
    upd_req = HTTPXRequest(connect_timeout=30.0, read_timeout=60.0,
                           write_timeout=30.0, pool_timeout=30.0)
    app = (Application.builder().token(TOKEN)
           .request(req).get_updates_request(upd_req)
           .post_init(_post_init).build())
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reglas", reglas))
    app.add_handler(ChatMemberHandler(on_new_member, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(MessageHandler((filters.TEXT | filters.CAPTION) & ~filters.COMMAND, on_message))
    return app


def main():
    app = build_application()
    space_host = os.environ.get("SPACE_HOST", "")
    if space_host:
        # MODO WEBHOOK: Telegram EMPUJA los updates a la URL pública del Space.
        # En Hugging Face el polling (jalar updates) se cuelga; el webhook usa la
        # conexión ENTRANTE (Telegram -> Space), que sí funciona. El server del webhook
        # ocupa el puerto 7860 (el que HF expone públicamente).
        path = "tg-update"
        secret = "hkn-" + TOKEN.split(":")[0]  # valida que el POST venga de Telegram
        url = "https://" + space_host + "/" + path
        log.info("Bot traductor+moderador corriendo (WEBHOOK -> %s). Idiomas: %s | Banear: %s",
                 url, TARGET_LANGS, BAN_CRYPTO)
        app.run_webhook(listen="0.0.0.0", port=7860, url_path=path,
                        webhook_url=url, secret_token=secret,
                        allowed_updates=Update.ALL_TYPES, drop_pending_updates=False)
    else:
        log.info("Bot traductor+moderador corriendo (POLLING local). Idiomas: %s | Banear: %s",
                 TARGET_LANGS, BAN_CRYPTO)
        app.run_polling(allowed_updates=Update.ALL_TYPES, bootstrap_retries=-1)


if __name__ == "__main__":
    main()
