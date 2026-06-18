# Calcpad Traductor + Moderador (Telegram bot)

Bot de Telegram que traduce ES⇄EN y modera grupos. Corre 24/7 en Koyeb (gratis).

## Deploy en Koyeb
1. Crear una cuenta en https://www.koyeb.com (login con GitHub, sin tarjeta en el plan Hobby).
2. **Create Service → GitHub →** este repo. Builder: **Dockerfile**.
3. Instancia: **Free** (Nano). Region: la que sea.
4. **Environment variables** (Settings):
   - `BOT_TOKEN` = el token de @BotFather (secreto).
   - `MM_EMAIL` = tu email (sube la cuota de MyMemory).
   - opcional: `TARGET_LANGS` (`es,en`), `BAN_CRYPTO` (`1`/`0`).
5. Deploy. El bot arranca en *polling* y queda activo 24/7.

El bot debe ser **administrador** del grupo (con "Eliminar mensajes" y "Bloquear usuarios"),
**no anónimo**, y con **privacidad desactivada** en @BotFather (`/setprivacy → Disable`).
