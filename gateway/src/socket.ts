import { mkdir, writeFile } from 'node:fs/promises'
import { join } from 'node:path'
import makeWASocket, {
  Browsers,
  DisconnectReason,
  fetchLatestBaileysVersion,
  isJidGroup,
  jidNormalizedUser,
  makeCacheableSignalKeyStore,
  useMultiFileAuthState,
} from 'baileys'
import type { GroupParticipant, WAMessage, WASocket } from 'baileys'
import QRCode from 'qrcode'
import { sendToBrain } from './brain.ts'
import { botSent, groupCache, messages } from './cache.ts'
import { config, isAllowedGroup } from './config.ts'
import { log, waLog } from './log.ts'
import { isBot, normalize } from './normalize.ts'
import type { BotIdentity } from './normalize.ts'

export type Status = 'starting' | 'qr' | 'connecting' | 'open' | 'closed' | 'logged_out' | 'replaced'

export const state = {
  sock: null as WASocket | null,
  status: 'starting' as Status,
  qr: null as string | null,
  pairingCode: null as string | null,
  bot: {} as BotIdentity,
  attempt: 0,
}

const unknownGroups = new Set<string>()
let reconnectTimer: NodeJS.Timeout | null = null
let pairingRequested = false

export const getSock = () => (state.status === 'open' ? state.sock : null)

async function groupName(jid: string) {
  const cached = groupCache.get(jid)
  if (cached) return cached.subject
  try {
    const meta = await state.sock!.groupMetadata(jid)
    groupCache.set(meta)
    return meta.subject
  } catch {
    return null
  }
}

function warnUnknown(jid: string, name?: string | null) {
  if (unknownGroups.has(jid)) return
  unknownGroups.add(jid)
  log.warn({ jid, name }, 'ignoring group that is not in ALLOWED_GROUP_JIDS (add it to .env to enable the bot there)')
}

const scheduleRead = (msgs: WAMessage[]) => {
  if (!config.readReceipts || !msgs.length) return
  const keys = msgs.map((m) => m.key)
  setTimeout(() => state.sock?.readMessages(keys).catch(() => undefined), 1500 + Math.random() * 6000)
}

const plainText = (msg: WAMessage) =>
  msg.message?.conversation ?? msg.message?.extendedTextMessage?.text ?? msg.message?.imageMessage?.caption ?? ''

const labelledByBot = (msg: WAMessage) => {
  const tag = config.botPrefix.trim()
  return !!tag && plainText(msg).startsWith(tag)
}

export const fromBot = (msg: WAMessage) =>
  !!msg.key.fromMe && (!config.sharedNumber || !!botSent.get(msg.key.id) || labelledByBot(msg))

export const catchUp = (msg: WAMessage, nowSeconds = Date.now() / 1000) =>
  nowSeconds - Number(msg.messageTimestamp ?? 0) < config.offlineCatchupMinutes * 60 && (!msg.key.fromMe || config.sharedNumber)

async function onMessages(msgs: WAMessage[]) {
  const toRead: WAMessage[] = []
  for (const msg of msgs) {
    if (msg.key.id) messages.set(msg.key.id, msg)
    const jid = msg.key.remoteJid
    if (!jid || !isJidGroup(jid) || fromBot(msg) || !msg.message) continue
    if (!isAllowedGroup(jid)) {
      warnUnknown(jid, groupCache.get(jid)?.subject)
      continue
    }
    const event = await normalize(msg, state.sock!, state.bot, await groupName(jid))
    if (!event) continue
    sendToBrain('/event', event)
    if (event.kind !== 'reaction') toRead.push(msg)
  }
  scheduleRead(toRead)
}

const participantIsBot = (p: GroupParticipant | string) =>
  typeof p === 'string' ? isBot(p, state.bot) : isBot(p.id, state.bot) || isBot(p.phoneNumber, state.bot) || isBot(p.lid, state.bot)

async function onParticipants(update: { id: string; participants: (GroupParticipant | string)[]; action: string }) {
  groupCache.drop(update.id)
  if (!update.participants.some(participantIsBot)) return
  const name = await groupName(update.id)
  if (!isAllowedGroup(update.id)) {
    warnUnknown(update.id, name)
    return
  }
  if (update.action === 'add') sendToBrain('/event', { type: 'bot_added', group_jid: update.id, group_name: name, ts: Date.now() / 1000 })
  if (update.action === 'remove') sendToBrain('/event', { type: 'bot_removed', group_jid: update.id, group_name: name, ts: Date.now() / 1000 })
}

async function warmGroups() {
  try {
    const all = await state.sock!.groupFetchAllParticipating()
    for (const meta of Object.values(all)) {
      groupCache.set(meta)
      if (isAllowedGroup(meta.id)) sendToBrain('/event', { type: 'group_update', group_jid: meta.id, group_name: meta.subject, ts: Date.now() / 1000 })
      else unknownGroups.add(meta.id)
    }
    const active = Object.values(all).filter((g) => isAllowedGroup(g.id)).map((g) => g.subject)
    log.info({ groups: Object.keys(all).length, active }, 'groups loaded (list all with GET /groups)')
  } catch (err) {
    log.warn({ err: (err as Error).message }, 'could not fetch groups')
  }
}

async function showQr(qr: string) {
  state.qr = qr
  state.status = 'qr'
  console.log(await QRCode.toString(qr, { type: 'terminal', small: true }))
  await QRCode.toFile(join(config.authDir, 'qr.png'), qr, { width: 512 }).catch(() => undefined)
  log.info(`scan the QR with WhatsApp > Linked devices (also at http://${config.host}:${config.port}/qr and ${join(config.authDir, 'qr.png')})`)
}

async function requestPairing() {
  if (pairingRequested || !config.pairingPhone) return
  pairingRequested = true
  try {
    state.pairingCode = await state.sock!.requestPairingCode(config.pairingPhone)
    log.info({ code: state.pairingCode }, 'enter this pairing code in WhatsApp > Linked devices > Link with phone number')
  } catch (err) {
    pairingRequested = false
    log.error({ err: (err as Error).message }, 'pairing code request failed')
  }
}

function scheduleReconnect(immediate = false) {
  if (reconnectTimer) return
  const delay = immediate ? 500 : Math.min(300_000, 2000 * 2 ** state.attempt) + Math.random() * 1000
  state.attempt++
  log.warn({ delay: Math.round(delay), attempt: state.attempt }, 'reconnecting')
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null
    connect().catch((err) => {
      log.error({ err: (err as Error).message }, 'connect failed')
      scheduleReconnect()
    })
  }, delay)
}

export async function connect() {
  await mkdir(config.authDir, { recursive: true })
  const { state: auth, saveCreds } = await useMultiFileAuthState(config.authDir)
  const latest = await fetchLatestBaileysVersion().catch(() => null)

  state.status = 'connecting'
  const sock = makeWASocket({
    auth: { creds: auth.creds, keys: makeCacheableSignalKeyStore(auth.keys, waLog) },
    ...(latest?.version ? { version: latest.version } : {}),
    logger: waLog,
    browser: Browsers.macOS('Chrome'),
    markOnlineOnConnect: false,
    syncFullHistory: false,
    generateHighQualityLinkPreview: false,
    getMessage: async (key) => messages.get(key.id)?.message ?? undefined,
    cachedGroupMetadata: async (jid) => groupCache.get(jid),
  })
  state.sock = sock

  sock.ev.on('creds.update', saveCreds)

  sock.ev.on('connection.update', async ({ connection, lastDisconnect, qr }) => {
    if (qr) {
      if (config.pairingPhone && !auth.creds.registered) await requestPairing()
      else await showQr(qr)
    }
    if (connection === 'open') {
      state.status = 'open'
      state.qr = null
      state.pairingCode = null
      state.attempt = 0
      state.bot = {
        pn: sock.user?.id ? jidNormalizedUser(sock.user.id) : undefined,
        lid: sock.user?.lid ? jidNormalizedUser(sock.user.lid) : undefined,
      }
      log.info({ bot: state.bot }, 'connected to WhatsApp')
      await warmGroups()
    }
    if (connection === 'close') {
      const code = (lastDisconnect?.error as { output?: { statusCode?: number } } | undefined)?.output?.statusCode
      state.sock = null
      if (code === DisconnectReason.loggedOut) {
        state.status = 'logged_out'
        log.error(`logged out. delete ${config.authDir} and restart to link again`)
        return
      }
      if (code === DisconnectReason.connectionReplaced) {
        state.status = 'replaced'
        log.error('another session replaced this one; not reconnecting to avoid a fight')
        return
      }
      state.status = 'closed'
      scheduleReconnect(code === DisconnectReason.restartRequired)
    }
  })

  sock.ev.on('messages.upsert', async ({ messages: msgs, type }) => {
    if (type !== 'notify') {
      for (const m of msgs) if (m.key.id) messages.set(m.key.id, m)
      const missed = msgs.filter((m) => catchUp(m) && !!m.key.remoteJid && isAllowedGroup(m.key.remoteJid))
      if (!missed.length) return
      log.info({ count: missed.length }, 'catching up on messages sent while the gateway was offline')
      msgs = missed
    }
    await onMessages(msgs).catch((err) => log.error({ err: (err as Error).message }, 'message handling failed'))
  })

  sock.ev.on('group-participants.update', (update) => {
    onParticipants(update).catch((err) => log.error({ err: (err as Error).message }, 'participant update failed'))
  })

  sock.ev.on('groups.update', (updates) => {
    for (const u of updates) {
      if (!u.id) continue
      if (u.subject) groupCache.patch(u.id, { subject: u.subject })
      if (u.subject && isAllowedGroup(u.id)) sendToBrain('/event', { type: 'group_update', group_jid: u.id, group_name: u.subject, ts: Date.now() / 1000 })
    }
  })

  sock.ev.on('groups.upsert', (metas) => {
    for (const meta of metas) groupCache.set(meta)
  })
}

export async function listGroups() {
  const sock = getSock()
  if (!sock) return null
  const all = await sock.groupFetchAllParticipating()
  return Object.values(all).map((g) => ({ jid: g.id, subject: g.subject, size: g.participants.length, allowed: isAllowedGroup(g.id) }))
}

export async function shutdown() {
  if (reconnectTimer) clearTimeout(reconnectTimer)
  state.sock?.end(undefined)
}
