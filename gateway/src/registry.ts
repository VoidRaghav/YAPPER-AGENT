import { discord } from './adapters/discord.ts'
import { telegram } from './adapters/telegram.ts'
import type { Adapter } from './adapters/types.ts'
import { whatsapp } from './adapters/whatsapp.ts'
import { config } from './config.ts'

export const adapters: Adapter[] = [
  ...(config.whatsappEnabled ? [whatsapp] : []),
  ...(config.telegramToken ? [telegram] : []),
  ...(config.discordToken ? [discord] : []),
]

export const adapterFor = (jid: string) => adapters.find((a) => a.owns(jid))
