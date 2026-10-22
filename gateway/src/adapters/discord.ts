import { setTimeout as sleep } from 'node:timers/promises'
import { AttachmentBuilder, ChannelType, Client, Events, GatewayIntentBits } from 'discord.js'
import type { Attachment, Message, SendableChannels } from 'discord.js'
import { sendToBrain } from '../brain.ts'
import { config, isAllowedGroup } from '../config.ts'
import { log } from '../log.ts'
import type { Media, MessageEvent, Quoted } from '../normalize.ts'
import type { Adapter, GroupInfo, SendJob } from './types.ts'

let client: Client | null = null
const seen = new Map<string, { title: string; size: number | null }>()
const warned = new Set<string>()

const jidOf = (channelId: string) => `dc:${channelId}`
const idOf = (channelId: string, messageId: string) => `dc:${channelId}:${messageId}`
const channelOf = (jid: string) => jid.split(':')[1]
const messageOf = (id: string) => id.split(':').pop()!

async function download(a: Attachment): Promise<string | null> {
  if (a.size > config.maxMediaBytes) return null
  try {
    const res = await fetch(a.url)
    return res.ok ? Buffer.from(await res.arrayBuffer()).toString('base64') : null
  } catch (err) {
    log.warn({ err: (err as Error).message }, 'discord media download failed')
    return null
  }
}

async function sendable(jid: string): Promise<SendableChannels> {
  const channel = await client?.channels.fetch(channelOf(jid))
  if (!channel?.isSendable()) throw new Error(`discord channel ${jid} is not sendable`)
  return channel
}

export async function toEvent(m: Message, botId: string): Promise<MessageEvent | null> {
  if (!m.inGuild() || m.author.bot) return null
  const jid = jidOf(m.channelId)
  const title = `${m.guild.name} #${'name' in m.channel ? m.channel.name : m.channelId}`
  seen.set(jid, { title, size: m.guild.memberCount ?? null })
  if (!isAllowedGroup(jid)) {
    if (!warned.has(jid)) {
      warned.add(jid)
      log.warn({ jid, name: title }, 'ignoring discord channel that is not in ALLOWED_GROUP_JIDS')
    }
    return null
  }

  let kind = 'text'
  let media: Media | null = null
  const a = m.attachments.first()
  if (a) {
    const type = a.contentType ?? ''
    if (type.startsWith('audio/')) {
      kind = m.flags.has(8192) ? 'voice' : 'audio'
      media = { mimetype: type, b64: await download(a), seconds: a.duration ?? null, filename: a.name }
    } else if (type.startsWith('image/')) {
      kind = type === 'image/gif' ? 'gif' : 'image'
      media = { mimetype: type, b64: kind === 'image' ? await download(a) : null, seconds: null, filename: a.name }
    } else if (type.startsWith('video/')) {
      kind = 'video'
    } else {
      kind = 'document'
      media = { mimetype: type || 'application/octet-stream', b64: null, seconds: null, filename: a.name }
    }
  } else if (m.stickers.size) {
    kind = 'sticker'
  } else if (!m.content) {
    return null
  }

  let quoted: Quoted | null = null
  if (m.reference?.messageId) {
    const ref = await m.fetchReference().catch(() => null)
    quoted = {
      id: idOf(m.channelId, m.reference.messageId),
      participant: ref ? `dc:${ref.author.id}` : null,
      from_bot: ref?.author.id === botId,
      text: ref?.content || null,
    }
  }

  return {
    type: 'message',
    group_jid: jid,
    group_name: title,
    id: idOf(m.channelId, m.id),
    sender_jid: `dc:${m.author.id}`,
    sender_alt_jid: null,
    sender_name: m.member?.displayName ?? m.author.globalName ?? m.author.username,
    ts: Math.floor(m.createdTimestamp / 1000),
    kind,
    text: m.cleanContent ?? m.content,
    mentions: [...m.mentions.users.keys()].filter((id) => id !== botId).map((id) => `dc:${id}`),
    mentioned_bot: m.mentions.users.has(botId) || !!quoted?.from_bot,
    from_owner: false,
    quoted,
    media,
    reaction: null,
  }
}

const file = (job: SendJob, name: string) => new AttachmentBuilder(Buffer.from(job.media_b64 ?? '', 'base64'), { name })

export const discord: Adapter = {
  name: 'discord',
  owns: (jid) => jid.startsWith('dc:'),
  ready: () => !!client?.isReady(),

  async start() {
    client = new Client({ intents: [GatewayIntentBits.Guilds, GatewayIntentBits.GuildMessages, GatewayIntentBits.MessageContent] })
    client.on(Events.MessageCreate, async (m) => {
      try {
        const ev = await toEvent(m, client!.user!.id)
        if (ev) sendToBrain('/event', ev)
      } catch (err) {
        log.error({ err: (err as Error).message }, 'discord message failed')
      }
    })
    client.on(Events.GuildCreate, (guild) => {
      for (const channel of guild.channels.cache.values()) {
        if (channel.type === ChannelType.GuildText) log.info({ jid: jidOf(channel.id), name: `${guild.name} #${channel.name}` }, 'discord channel available')
      }
    })
    client.once(Events.ClientReady, (c) => log.info({ bot: c.user.tag, servers: c.guilds.cache.size }, 'discord bot connected'))
    await client.login(config.discordToken)
  },

  async typing(jid, _kind, ms) {
    const channel = await sendable(jid).catch(() => null)
    if (!channel) return
    let remaining = ms
    while (remaining > 0) {
      await channel.sendTyping().catch(() => undefined)
      const step = Math.min(remaining, 8000)
      await sleep(step)
      remaining -= step
    }
  },

  async deliver(job) {
    const channel = await sendable(job.jid)
    if (job.kind === 'reaction') {
      const target = await channel.messages.fetch(messageOf(job.quote!.id))
      await target.react(job.emoji ?? '👍')
      return null
    }
    const reply = job.quote?.id ? { reply: { messageReference: messageOf(job.quote.id), failIfNotExists: false } } : {}
    const quiet = { allowedMentions: { repliedUser: false } }
    const sent =
      job.kind === 'voice'
        ? await channel.send({ files: [file(job, 'voice-note.ogg')], ...reply, ...quiet })
        : job.kind === 'image'
          ? await channel.send({ content: job.text || undefined, files: [file(job, 'meme.jpg')], ...reply, ...quiet })
          : job.kind === 'sticker'
            ? await channel.send({ files: [file(job, 'sticker.webp')], ...reply, ...quiet })
            : await channel.send({ content: job.text ?? '', ...reply, ...quiet })
    return idOf(sent.channelId, sent.id)
  },

  async groups(): Promise<GroupInfo[]> {
    return [...seen].map(([jid, g]) => ({ jid, subject: g.title, size: g.size, allowed: isAllowedGroup(jid), platform: 'discord' }))
  },

  status: () => ({ status: client?.isReady() ? 'open' : 'starting', bot: client?.user?.tag ?? null }),

  async stop() {
    await client?.destroy()
  },
}
