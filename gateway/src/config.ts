import { existsSync } from 'node:fs'
import { resolve } from 'node:path'

const rootEnv = resolve(import.meta.dirname, '../../.env')
if (existsSync(rootEnv)) process.loadEnvFile(rootEnv)

const env = process.env
const list = (value?: string) => (value ?? '').split(',').map((s) => s.trim()).filter(Boolean)
const bool = (value: string | undefined, fallback: boolean) => (value === undefined || value === '' ? fallback : ['1', 'true', 'yes', 'on'].includes(value.toLowerCase()))

export const config = {
  host: env.GATEWAY_HOST ?? '127.0.0.1',
  port: Number(env.GATEWAY_PORT ?? 3001),
  brainUrl: (env.BRAIN_URL ?? 'http://127.0.0.1:8000').replace(/\/$/, ''),
  token: env.INTERNAL_TOKEN ?? '',
  authDir: resolve(env.GATEWAY_AUTH_DIR ?? resolve(import.meta.dirname, '../auth')),
  allowedGroups: new Set(list(env.ALLOWED_GROUP_JIDS)),
  botName: env.BOT_NAME ?? 'Yapper',
  pairingPhone: (env.PAIRING_PHONE ?? '').replace(/\D/g, ''),
  whatsappEnabled: bool(env.WHATSAPP_ENABLED, true),
  telegramToken: env.TELEGRAM_BOT_TOKEN ?? '',
  discordToken: env.DISCORD_BOT_TOKEN ?? '',
  sharedNumber: bool(env.SHARED_NUMBER, false),
  botPrefix: env.SHARED_BOT_PREFIX ?? '🤖 ',
  ownerName: env.OWNER_NAME || null,
  readReceipts: bool(env.READ_RECEIPTS, !bool(env.SHARED_NUMBER, false)),
  maxMediaBytes: Number(env.MAX_MEDIA_BYTES ?? 8_000_000),
  maxSendsPerHour: Number(env.GATEWAY_MAX_SENDS_PER_HOUR ?? 60),
  minSendGapMs: Number(env.GATEWAY_MIN_SEND_GAP_MS ?? 1500),
  offlineCatchupMinutes: Number(env.OFFLINE_CATCHUP_MINUTES ?? 360),
  logLevel: env.GATEWAY_LOG_LEVEL ?? 'info',
  waLogLevel: env.BAILEYS_LOG_LEVEL ?? 'warn',
}

export const isAllowedGroup = (jid: string) => config.allowedGroups.has(jid)
