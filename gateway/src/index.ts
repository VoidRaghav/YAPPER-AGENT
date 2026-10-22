import { config } from './config.ts'
import { log } from './log.ts'
import { adapters } from './registry.ts'
import { startServer } from './server.ts'

if (!adapters.length) log.warn('no platform enabled: set WHATSAPP_ENABLED=true, TELEGRAM_BOT_TOKEN or DISCORD_BOT_TOKEN')
if (!config.allowedGroups.size) log.warn('ALLOWED_GROUP_JIDS is empty: the bot will only log group ids until you allow-list one (see GET /groups)')
if (!config.token) log.warn('INTERNAL_TOKEN is empty: gateway endpoints are unauthenticated')

const server = startServer()

for (const adapter of adapters) {
  adapter.start().then(
    () => log.info({ platform: adapter.name }, 'platform started'),
    (err) => log.error({ platform: adapter.name, err: (err as Error).message }, 'platform failed to start'),
  )
}

const stop = async (signal: string) => {
  log.info({ signal }, 'shutting down')
  await Promise.allSettled(adapters.map((a) => a.stop()))
  server.close(() => process.exit(0))
  setTimeout(() => process.exit(0), 3000).unref()
}

process.on('SIGINT', () => void stop('SIGINT'))
process.on('SIGTERM', () => void stop('SIGTERM'))
process.on('unhandledRejection', (err) => log.error({ err: (err as Error)?.message }, 'unhandled rejection'))
