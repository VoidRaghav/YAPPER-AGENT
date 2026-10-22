import { areJidsSameUser, downloadMediaMessage, getContentType, jidNormalizedUser, normalizeMessageContent } from 'baileys'
import type { WAMessage, WASocket, proto } from 'baileys'
import { botSent } from './cache.ts'
import { config } from './config.ts'
import { log, waLog } from './log.ts'

export type BotIdentity = { pn?: string; lid?: string }

export type Quoted = { id: string; participant: string | null; from_bot: boolean; text: string | null }
export type Media = { mimetype: string; b64: string | null; seconds: number | null; filename: string | null }

export type MessageEvent = {
  type: 'message'
  group_jid: string
  group_name: string | null
  id: string
  sender_jid: string
  sender_alt_jid: string | null
  sender_name: string | null
  ts: number
  kind: string
  text: string
  mentions: string[]
  mentioned_bot: boolean
  from_owner: boolean
  quoted: Quoted | null
  media: Media | null
  reaction: { target_id: string; emoji: string } | null
}

export const isBot = (jid: string | null | undefined, bot: BotIdentity) =>
  !!jid && ((!!bot.pn && areJidsSameUser(jid, bot.pn)) || (!!bot.lid && areJidsSameUser(jid, bot.lid)))

const textOf = (content: proto.IMessage | null | undefined): string => {
  const inner = normalizeMessageContent(content)
  if (!inner) return ''
  return (
    inner.conversation ??
    inner.extendedTextMessage?.text ??
    inner.imageMessage?.caption ??
    inner.videoMessage?.caption ??
    inner.documentMessage?.caption ??
    inner.pollCreationMessageV3?.name ??
    inner.pollCreationMessage?.name ??
    ''
  )
}

const pollText = (poll: proto.Message.IPollCreationMessage | null | undefined) =>
  poll ? `${poll.name ?? ''} [${(poll.options ?? []).map((o) => o.optionName).filter(Boolean).join(' / ')}]` : ''

async function download(msg: WAMessage, sock: WASocket, bytes: number | null | undefined) {
  if (bytes && bytes > config.maxMediaBytes) return null
  try {
    const buffer = await downloadMediaMessage(msg, 'buffer', {}, { logger: waLog, reuploadRequest: sock.updateMediaMessage })
    return buffer.length > config.maxMediaBytes ? null : buffer.toString('base64')
  } catch (err) {
    log.warn({ id: msg.key.id, err: (err as Error).message }, 'media download failed')
    return null
  }
}

export async function normalize(msg: WAMessage, sock: WASocket, bot: BotIdentity, groupName: string | null): Promise<MessageEvent | null> {
  const content = normalizeMessageContent(msg.message)
  const type = getContentType(content)
  if (!content || !type || !msg.key.id || !msg.key.remoteJid) return null

  const own = !!msg.key.fromMe
  const sender = msg.key.participant ?? (own ? (bot.pn ?? bot.lid ?? '') : '')
  const senderAlt = msg.key.participantAlt ?? (own && bot.pn && bot.lid ? bot.lid : null)
  if (!sender) return null

  const ts = Number(msg.messageTimestamp ?? Math.floor(Date.now() / 1000))
  let kind = 'text'
  let text = ''
  let media: Media | null = null
  let reaction: MessageEvent['reaction'] = null
  let ctx: proto.IContextInfo | null | undefined

  switch (type) {
    case 'conversation':
      text = content.conversation ?? ''
      break
    case 'extendedTextMessage':
      text = content.extendedTextMessage?.text ?? ''
      ctx = content.extendedTextMessage?.contextInfo
      break
    case 'imageMessage': {
      const m = content.imageMessage!
      kind = 'image'
      text = m.caption ?? ''
      ctx = m.contextInfo
      media = { mimetype: m.mimetype ?? 'image/jpeg', b64: await download(msg, sock, Number(m.fileLength ?? 0)), seconds: null, filename: null }
      break
    }
    case 'videoMessage': {
      const m = content.videoMessage!
      kind = m.gifPlayback ? 'gif' : 'video'
      text = m.caption ?? ''
      ctx = m.contextInfo
      media = { mimetype: m.mimetype ?? 'video/mp4', b64: null, seconds: m.seconds ?? null, filename: null }
      break
    }
    case 'audioMessage': {
      const m = content.audioMessage!
      kind = m.ptt ? 'voice' : 'audio'
      ctx = m.contextInfo
      media = { mimetype: m.mimetype ?? 'audio/ogg; codecs=opus', b64: await download(msg, sock, Number(m.fileLength ?? 0)), seconds: m.seconds ?? null, filename: null }
      break
    }
    case 'stickerMessage':
      kind = 'sticker'
      ctx = content.stickerMessage?.contextInfo
      break
    case 'documentMessage':
    case 'documentWithCaptionMessage': {
      const m = content.documentMessage ?? content.documentWithCaptionMessage?.message?.documentMessage
      kind = 'document'
      text = m?.caption ?? ''
      ctx = m?.contextInfo
      media = { mimetype: m?.mimetype ?? 'application/octet-stream', b64: null, seconds: null, filename: m?.fileName ?? null }
      break
    }
    case 'pollCreationMessage':
    case 'pollCreationMessageV2':
    case 'pollCreationMessageV3':
      kind = 'poll'
      text = pollText(content.pollCreationMessage ?? content.pollCreationMessageV2 ?? content.pollCreationMessageV3)
      break
    case 'locationMessage':
    case 'liveLocationMessage':
      kind = 'location'
      break
    case 'contactMessage':
    case 'contactsArrayMessage':
      kind = 'contact'
      break
    case 'reactionMessage': {
      const r = content.reactionMessage!
      if (!r.key?.id) return null
      kind = 'reaction'
      reaction = { target_id: r.key.id, emoji: r.text ?? '' }
      break
    }
    default:
      return null
  }

  const shared = config.sharedNumber
  const mentioned = (ctx?.mentionedJid ?? []).map((j) => jidNormalizedUser(j))
  const mentions = shared ? mentioned : mentioned.filter((j) => !isBot(j, bot))
  const quotedParticipant = ctx?.participant ? jidNormalizedUser(ctx.participant) : null
  const quotedFromBot = ctx?.stanzaId ? (shared ? !!botSent.get(ctx.stanzaId) : isBot(quotedParticipant, bot)) : false
  const quoted: Quoted | null = ctx?.stanzaId
    ? { id: ctx.stanzaId, participant: quotedParticipant, from_bot: quotedFromBot, text: textOf(ctx.quotedMessage) || null }
    : null

  return {
    type: 'message',
    group_jid: msg.key.remoteJid,
    group_name: groupName,
    id: msg.key.id,
    sender_jid: jidNormalizedUser(sender),
    sender_alt_jid: senderAlt ? jidNormalizedUser(senderAlt) : null,
    sender_name: msg.pushName ?? (own ? config.ownerName : null),
    ts,
    kind,
    text,
    mentions,
    mentioned_bot: quotedFromBot || (!shared && mentioned.length > mentions.length),
    from_owner: own,
    quoted,
    media,
    reaction,
  }
}
