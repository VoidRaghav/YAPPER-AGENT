import { setTimeout as sleep } from 'node:timers/promises'
import type { SendJob, SendKind } from './adapters/types.ts'
import { sendToBrain } from './brain.ts'
import { config } from './config.ts'
import { log } from './log.ts'
import { adapterFor } from './registry.ts'

export type { SendJob, SendKind } from './adapters/types.ts'

const kinds = new Set<SendKind>(['text', 'reaction', 'voice', 'image', 'sticker'])
const chains = new Map<string, Promise<unknown>>()
const sentAt: number[] = []
let lastSend = 0

export function validate(body: unknown): SendJob | string {
  const job = body as Partial<SendJob>
  if (!job || typeof job.jid !== 'string' || !adapterFor(job.jid)) return 'jid must belong to a connected platform'
  if (!job.kind || !kinds.has(job.kind)) return 'invalid kind'
  if (job.kind === 'text' && !job.text) return 'text required'
  if (job.kind === 'reaction' && (!job.emoji || !job.quote?.id)) return 'emoji and quote.id required'
  if (['voice', 'image', 'sticker'].includes(job.kind) && !job.media_b64) return 'media_b64 required'
  return job as SendJob
}

const withinHourlyCap = () => {
  const cutoff = Date.now() - 3_600_000
  while (sentAt.length && sentAt[0] < cutoff) sentAt.shift()
  return sentAt.length < config.maxSendsPerHour
}

async function run(job: SendJob) {
  const adapter = adapterFor(job.jid)
  if (!adapter?.ready()) throw new Error(`${adapter?.name ?? 'platform'} not connected`)
  const delay = Math.max(0, Math.min(job.delay_ms ?? 0, 60_000))

  if (job.kind === 'reaction') await sleep(delay)
  else if (delay > 0) await adapter.typing(job.jid, job.kind, delay)

  const gap = config.minSendGapMs - (Date.now() - lastSend)
  if (gap > 0) await sleep(gap)
  if (!withinHourlyCap()) {
    log.warn({ jid: job.jid, kind: job.kind }, 'hourly send cap reached, dropping')
    sendToBrain('/sent', { ref: job.ref, jid: job.jid, wa_id: null, kind: job.kind, ok: false, error: 'hourly_cap' }, 2)
    return
  }

  const id = await adapter.deliver(job)
  lastSend = Date.now()
  sentAt.push(lastSend)
  log.info({ platform: adapter.name, jid: job.jid, kind: job.kind, id }, 'sent')
  sendToBrain('/sent', { ref: job.ref, jid: job.jid, wa_id: id, kind: job.kind, ok: true }, 3)
}

export function enqueue(job: SendJob) {
  const prev = chains.get(job.jid) ?? Promise.resolve()
  const next = prev
    .then(() => run(job))
    .catch((err) => {
      log.error({ jid: job.jid, kind: job.kind, err: (err as Error).message }, 'send failed')
      sendToBrain('/sent', { ref: job.ref, jid: job.jid, wa_id: null, kind: job.kind, ok: false, error: (err as Error).message }, 2)
    })
  chains.set(job.jid, next)
  next.finally(() => {
    if (chains.get(job.jid) === next) chains.delete(job.jid)
  })
}

export const pendingSends = () => chains.size
