import { setTimeout as sleep } from 'node:timers/promises'
import { Bot, InputFile } from 'grammy'
import type { Message, ReactionTypeEmoji, UserFromGetMe } from 'grammy/types'
import { sendToBrain } from '../brain.ts'
import { config, isAllowedGroup } from '../config.ts'
import { log } from '../log.ts'
import type { Media, MessageEvent, Quoted } from '../normalize.ts'
import type { Adapter, GroupInfo, SendJob } from './types.ts'

type Emoji = ReactionTypeEmoji['emoji']

const ALLOWED = new Set<string>([
  '👍', '👎', '❤', '🔥', '🥰', '👏', '😁', '🤔', '🤯', '😱', '😢', '🎉', '🤩', '🙏', '👌', '🤡', '🥱', '😍', '💯', '🤣', '⚡', '🏆',
  '💔', '🤨', '😐', '😭', '🤓', '👀', '🙈', '😇', '🤝', '🤗', '🫡', '🗿', '😎', '🤷',
])
const NEAREST: Record<string, string> = { '😂': '🤣', '💀': '🤣', '😆': '🤣', '🫂': '🤗', '💪': '🔥', '🙌': '👏', '🥲': '😭' }

let bot: Bot | null = null
let me: UserFromGetMe | null = null
const seen = new Map<string, { title: string; size: number | null }>()
const warned = new Set<string>()

const jidOf = (chatId: number | string) => `tg:${chatId}`
const idOf = (chatId: number | string, messageId: number) => `tg:${chatId}:${messageId}`
const chatOf = (jid: string) => jid.slice(3)
const messageOf = (id: string) => Number(id.split(':').pop())

export function reactionFor(emoji: string): Emoji {
  const plain = emoji.replace(/️/g, '')
  const pick = ALLOWED.has(plain) ? plain : NEAREST[plain] ?? '👍'
  return pick as Emoji
}

async function download(fileId: string, size?: number): Promise<string | null> {
  if (!bot || (size && size > config.maxMediaBytes)) return null
  try {
    const file = await bot.api.getFile(fileId)
    if (!file.file_path) return null
    const res = await fetch(`https://api.telegram.org/file/bot${config.telegramToken}/${file.file_path}`)
    if (!res.ok) return null
    const buf = Buffer.from(await res.arrayBuffer())
    return buf.length > config.maxMediaBytes ? null : buf.toString('base64')
  } catch (err) {
    log.warn({ err: (err as Error).message }, 'telegram media download failed')
    return null
  }
}

const nameOf = (m: Message) =>
  [m.from?.first_name, m.from?.last_name].filter(Boolean).join(' ') || m.from?.username || null

export async function toEvent(m: Message, self: UserFromGetMe): Promise<MessageEvent | null> {
  if (m.chat.type !== 'group' && m.chat.type !== 'supergroup') return null
  const jid = jidOf(m.chat.id)
  seen.set(jid, { title: m.chat.title, size: null })
  if (!isAllowedGroup(jid)) {
    if (!warned.has(jid)) {
      warned.add(jid)
      log.warn({ jid, name: m.chat.title }, 'ignoring telegram group that is not in ALLOWED_GROUP_JIDS')
    }
    return null
  }
  if (m.new_chat_title) {
    sendToBrain('/event', { type: 'group_update', group_jid: jid, group_name: m.new_chat_title, ts: m.date })
    return null
  }
  if (!m.from || m.from.id === self.id) return null

  let kind = 'text'
  let media: Media | null = null
  const text = m.text ?? m.caption ?? ''
  if (m.voice) {
    kind = 'voice'
    media = { mimetype: m.voice.mime_type ?? 'audio/ogg', b64: await download(m.voice.file_id, m.voice.file_size), seconds: m.voice.duration, filename: null }
  } else if (m.audio) {
    kind = 'audio'
    media = { mimetype: m.audio.mime_type ?? 'audio/mpeg', b64: null, seconds: m.audio.duration, filename: m.audio.file_name ?? null }
  } else if (m.photo?.length) {
    const best = m.photo[m.photo.length - 1]
    kind = 'image'
    media = { mimetype: 'image/jpeg', b64: await download(best.file_id, best.file_size), seconds: null, filename: null }
  } else if (m.sticker) {
    kind = 'sticker'
  } else if (m.animation) {
    kind = 'gif'
  } else if (m.video) {
    kind = 'video'
  } else if (m.document) {
    kind = 'document'
    media = { mimetype: m.document.mime_type ?? 'application/octet-stream', b64: null, seconds: null, filename: m.document.file_name ?? null }
  } else if (m.poll) {
    kind = 'poll'
  } else if (m.location) {
    kind = 'location'
  } else if (m.contact) {
    kind = 'contact'
  } else if (!text) {
    return null
  }

  const entities = m.entities ?? m.caption_entities ?? []
  const handle = `@${self.username}`.toLowerCase()
  let mentionedBot = false
  const mentions: string[] = []
  for (const e of entities) {
    if (e.type === 'mention' && text.slice(e.offset, e.offset + e.length).toLowerCase() === handle) mentionedBot = true
    if (e.type === 'text_mention' && e.user.id !== self.id) mentions.push(`tg:${e.user.id}`)
  }

  const r = m.reply_to_message
  const quoted: Quoted | null = r
    ? { id: idOf(m.chat.id, r.message_id), participant: r.from ? `tg:${r.from.id}` : null, from_bot: r.from?.id === self.id, text: r.text ?? r.caption ?? null }
    : null

  return {
    type: 'message',
    group_jid: jid,
    group_name: m.chat.title,
    id: idOf(m.chat.id, m.message_id),
    sender_jid: `tg:${m.from.id}`,
    sender_alt_jid: null,
    sender_name: nameOf(m),
    ts: m.date,
    kind,
    text: m.poll ? `${m.poll.question} [${m.poll.options.map((o) => o.text).join(' / ')}]` : text,
    mentions,
    mentioned_bot: mentionedBot || !!quoted?.from_bot,
    from_owner: false,
    quoted,
    media,
    reaction: null,
  }
}

const file = (job: SendJob, name: string) => new InputFile(Buffer.from(job.media_b64 ?? '', 'base64'), name)

export const telegram: Adapter = {
  name: 'telegram',
  owns: (jid) => jid.startsWith('tg:'),
  ready: () => !!bot && !!me,

  async start() {
    bot = new Bot(config.telegramToken)
    bot.catch((err) => log.error({ err: (err.error as Error)?.message }, 'telegram update failed'))
    bot.on('message', async (ctx) => {
      const ev = await toEvent(ctx.message, ctx.me)
      if (ev) sendToBrain('/event', ev)
    })
    bot.on('my_chat_member', (ctx) => {
      const jid = jidOf(ctx.chat.id)
      const now = ctx.myChatMember.new_chat_member.status
      const before = ctx.myChatMember.old_chat_member.status
      const title = 'title' in ctx.chat ? ctx.chat.title : null
      if (!isAllowedGroup(jid)) {
        log.warn({ jid, name: title }, 'telegram bot added to a group that is not in ALLOWED_GROUP_JIDS')
        return
      }
      const inside = (s: string) => s === 'member' || s === 'administrator'
      if (inside(now) && !inside(before)) sendToBrain('/event', { type: 'bot_added', group_jid: jid, group_name: title, ts: ctx.myChatMember.date })
      if (!inside(now) && inside(before)) sendToBrain('/event', { type: 'bot_removed', group_jid: jid, group_name: title, ts: ctx.myChatMember.date })
    })
    await bot.init()
    me = bot.botInfo
    log.info({ bot: me.username, privacy_off: me.can_read_all_group_messages }, 'telegram bot connected')
    if (!me.can_read_all_group_messages) {
      log.warn('telegram privacy mode is ON: the bot only sees commands and mentions. Turn it off with BotFather /setprivacy, then re-add the bot to the group')
    }
    void bot.start({ allowed_updates: ['message', 'my_chat_member'], drop_pending_updates: true })
  },

  async typing(jid, kind, ms) {
    if (!bot) return
    const action = kind === 'voice' ? 'record_voice' : kind === 'image' || kind === 'sticker' ? 'upload_photo' : 'typing'
    let remaining = ms
    while (remaining > 0) {
      await bot.api.sendChatAction(chatOf(jid), action).catch(() => undefined)
      const step = Math.min(remaining, 4500)
      await sleep(step)
      remaining -= step
    }
  },

  async deliver(job) {
    if (!bot) throw new Error('telegram not connected')
    const chat = chatOf(job.jid)
    const reply = job.quote?.id ? { reply_parameters: { message_id: messageOf(job.quote.id), allow_sending_without_reply: true } } : {}
    if (job.kind === 'reaction') {
      await bot.api.setMessageReaction(chat, messageOf(job.quote!.id), [{ type: 'emoji', emoji: reactionFor(job.emoji ?? '👍') }])
      return null
    }
    const sent =
      job.kind === 'voice'
        ? await bot.api.sendVoice(chat, file(job, 'voice.ogg'), reply)
        : job.kind === 'image'
          ? await bot.api.sendPhoto(chat, file(job, 'meme.jpg'), { caption: job.text || undefined, ...reply })
          : job.kind === 'sticker'
            ? await bot.api.sendSticker(chat, file(job, 'sticker.webp'), reply)
            : await bot.api.sendMessage(chat, job.text ?? '', reply)
    return idOf(chat, sent.message_id)
  },

  async groups(): Promise<GroupInfo[]> {
    return [...seen].map(([jid, g]) => ({ jid, subject: g.title, size: g.size, allowed: isAllowedGroup(jid), platform: 'telegram' }))
  },

  status: () => ({ status: me ? 'open' : 'starting', bot: me?.username ?? null, reads_all_messages: me?.can_read_all_group_messages ?? null }),

  async stop() {
    await bot?.stop()
  },
}
