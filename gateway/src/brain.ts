import { setTimeout as sleep } from 'node:timers/promises'
import { config } from './config.ts'
import { log } from './log.ts'

const maxQueue = 2000
let chain: Promise<unknown> = Promise.resolve()
let pending = 0

const headers = () => ({
  'content-type': 'application/json',
  ...(config.token ? { 'x-internal-token': config.token } : {}),
})

async function post(path: string, body: unknown, attempts: number) {
  for (let attempt = 0; attempt < attempts; attempt++) {
    try {
      const res = await fetch(`${config.brainUrl}${path}`, {
        method: 'POST',
        headers: headers(),
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(20_000),
      })
      if (res.ok) return true
      if (res.status < 500) {
        log.warn({ path, status: res.status }, 'brain rejected payload')
        return false
      }
    } catch (err) {
      log.warn({ path, attempt, err: (err as Error).message }, 'brain unreachable')
    }
    await sleep(Math.min(30_000, 1000 * 2 ** attempt))
  }
  log.error({ path }, 'dropping payload after retries')
  return false
}

export function sendToBrain(path: string, body: unknown, attempts = 6) {
  if (pending >= maxQueue) {
    log.error({ path }, 'brain queue full, dropping payload')
    return
  }
  pending++
  chain = chain.then(() => post(path, body, attempts)).finally(() => pending--)
}

export const brainQueueSize = () => pending
