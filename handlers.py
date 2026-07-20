"""Manejadores de comandos del bot de Telegram."""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

# ── Comandos básicos ────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bienvenida e instrucciones básicas."""
    await update.message.reply_text(
        "🤖 *AI SysAdmin Homelab*\n\n"
        "Sistema de monitorización read-only para tu clúster Proxmox.\n\n"
        "Comandos:\n"
        "• /health — Reporte de salud del homelab\n"
        "• /vm <id> — Diagnóstico de VM específica\n"
        "• /help — Esta ayuda\n\n"
        "🔒 *Modo read-only* — Solo diagnóstico, sin operaciones.",
        parse_mode="Markdown",
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ayuda detallada."""
    await update.message.reply_text(
        "📋 *Comandos disponibles*\n\n"
        "*/health* — Muestra el estado actual de todos los nodos, "
        "VMs, storage y eventos críticos de Docker.\n\n"
        "*/vm <id>* — Diagnóstico detallado de una máquina virtual "
        "o contenedor (CPU, RAM, uptime, snapshots).\n\n"
        "*/start* — Mensaje de bienvenida\n"
        "*/help* — Esta ayuda\n\n"
        "⚙️ *Próximamente (Phase 3):*\n"
        "• Aprobación de operaciones vía inline keyboards\n"
        "• Niveles de autonomía configurables",
        parse_mode="Markdown",
    )

# ── /health — Reporte de salud (stub, implementación real en 02-02) ──

async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reporte de salud del homelab."""
    try:
        reporter = context.application.bot_data.get("health_reporter")
        if reporter and hasattr(reporter, "collect_all_health"):
            report = await reporter.collect_all_health()
            await update.message.reply_text(
                report or "📊 Recopilando datos de salud...\n(implementación completa en Phase 2)",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text(
                "📊 *Estado del Homelab*\n\n"
                "📡 Conectando con Proxmox...\n"
                "🐳 Revisando contenedores Docker...\n\n"
                "*Nota:* El recolector de datos completo estará disponible "
                "en la siguiente fase de implementación.\n\n"
                "🟢 Bot operativo y escuchando comandos.",
                parse_mode="Markdown",
            )
    except Exception as e:
        logger.error(f"Error en cmd_health: {e}")
        await update.message.reply_text("❌ Error al obtener reporte de salud.")

# ── /vm <id> — Diagnóstico de VM (stub, implementación real en 02-02) ──

async def cmd_vm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Diagnóstico de máquina virtual o contenedor."""
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(
            "⚠️ Uso: /vm <ID>\n\n"
            "Ejemplo: /vm 100\n\n"
            "El ID es el número de la VM o contenedor en Proxmox.",
            parse_mode="Markdown",
        )
        return

    vmid = int(context.args[0])
    try:
        diagnostics = context.application.bot_data.get("vm_diagnostics")
        if diagnostics and hasattr(diagnostics, "diagnose"):
            result = await diagnostics.diagnose(vmid)
            await update.message.reply_text(
                result or f"🔍 Diagnóstico de VM {vmid}...\n(implementación completa en Phase 2)",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text(
                f"🔍 *Diagnóstico VM {vmid}*\n\n"
                "Consultando Proxmox API...\n\n"
                "*Nota:* El diagnósticador completo estará disponible "
                "en la siguiente fase de implementación.\n\n"
                f"✅ VM {vmid} registrada en el clúster.",
                parse_mode="Markdown",
            )
    except Exception as e:
        logger.error(f"Error en cmd_vm: {e}")
        await update.message.reply_text(
            f"❌ Error al obtener diagnóstico de VM {vmid}. "
            "Verifica que el ID existe.",
        )

# ── Phase 3 Scaffold: Callback para inline keyboards ──────────

async def approval_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manejador de callbacks para aprobación HITL (Phase 3).

    Este manejador se activa cuando el usuario pulsa un botón
    de inline keyboard con callback_data que empieza por 'approve:' o 'reject:'.

    En Phase 2 solo existe la estructura. En Phase 3 se completará
    con la lógica de aprobación HITL real.
    """
    query = update.callback_query
    await query.answer()

    action, payload = query.data.split(":", 1)

    if action == "approve":
        await query.edit_message_text(
            f"✅ *Aprobado*\n\nOperación {payload} aprobada.\n\n"
            "*(Funcionalidad completa en Phase 3 — HITL)*",
            parse_mode="Markdown",
        )
    elif action == "reject":
        await query.edit_message_text(
            f"❌ *Denegado*\n\nOperación {payload} rechazada.\n\n"
            "*(Funcionalidad completa en Phase 3 — HITL)*",
            parse_mode="Markdown",
        )
    else:
        await query.edit_message_text(f"Acción desconocida: {action}")
