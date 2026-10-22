import { setTimeout as sleep } from 'node:timers/promises'
import { generateMessageIDV2 } from 'baileys'
import type { AnyMessageContent, MiscMessageGenerationOptions, WAMessageKey, WAPresence } from 'baileys'
import { botSent, messages } from '../cache.ts'
import { config } from '../config.ts'
import { connect, getSock, listGroups, shutdown, state } from '../socket.ts'
import type { Adapter, SendJob } from './types.ts'

const label = (text: string | undefined) => (config.sharedNumber ? `${config.botPrefix}${text ?? ''}`.trim() : text)

function quoteKey(job: SendJob): WAMessageKey | null {
  if (!job.quote?.id) return null
  const cached = messages.get(job.quote.id)
  if (cached) return cached.key
  return { remoteJid: job.jid, id: job.quote.id, participant: job.quote.participant ?? undefined, fromMe: job.quote.from_me ?? false }
}

function build(job: SendJob): AnyMessageContent {
  const buffer = () => Buffer.from(job.media_b64 ?? '', 'base64')
  switch (job.kind) {
    case 'reaction':
      return { react: { text: job.emoji ?? '', key: quoteKey(job)! } }
    case 'voice':
      return { audio: buffer(), mimetype: job.mimetype ?? 'audio/ogg; codecs=opus', ptt: true }
    case 'image':
      return { image: buffer(), caption: label(job.text) || undefined }
    case 'sticker':
      return { sticker: buffer() }
    default:
      return { text: label(job.text) ?? '' }
  }
}

export const whatsapp: Adapter = {
  name: 'whatsapp',
  owns: (jid) => jid.endsWith('@g.us'),
  start: connect,
  ready: () => !!getSock(),

  async typing(jid, kind, ms) {
    const sock = getSock()
    if (!sock) return
    const presence: WAPresence = kind === 'voice' ? 'recording' : 'composing'
    await sock.presenceSubscribe(jid).catch(() => undefined)
    let remaining = ms
    while (remaining > 0) {
      await sock.sendPresenceUpdate(presence, jid).catch(() => undefined)
      const step = Math.min(remaining, 8000)
      await sleep(step)
      remaining -= step
    }
    await sock.sendPresenceUpdate('paused', jid).catch(() => undefined)
  },

  async deliver(job) {
    const sock = getSock()
    if (!sock) throw new Error('whatsapp not connected')
    const messageId = generateMessageIDV2(sock.user?.id)
    botSent.set(messageId, true)
    const options: MiscMessageGenerationOptions = { messageId }
    const quoted = job.kind !== 'reaction' && job.quote?.id ? messages.get(job.quote.id) : undefined
    if (quoted) options.quoted = quoted
    const sent = await sock.sendMessage(job.jid, build(job), options)
    if (sent?.key.id) messages.set(sent.key.id, sent)
    return sent?.key.id ?? null
  },

  async groups() {
    return ((await listGroups().catch(() => null)) ?? []).map((g) => ({ ...g, platform: 'whatsapp' }))
  },

  status: () => ({ status: state.status, bot: state.bot, pairing_code: state.pairingCode }),
  stop: shutdown,
}
