import express from 'express'
import type { NextFunction, Request, Response } from 'express'
import QRCode from 'qrcode'
import { brainQueueSize } from './brain.ts'
import { config } from './config.ts'
import { log } from './log.ts'
import { adapterFor, adapters } from './registry.ts'
import { enqueue, pendingSends, validate } from './send.ts'
import { state } from './socket.ts'

const authorized = (req: Request) => !config.token || req.get('x-internal-token') === config.token || req.query.token === config.token

const guard = (req: Request, res: Response, next: NextFunction) => {
  if (!authorized(req)) {
    res.status(401).json({ error: 'unauthorized' })
    return
  }
  next()
}

export function startServer() {
  const app = express()
  app.use(express.json({ limit: '30mb' }))

  app.get('/health', (_req, res) => {
    const platforms = Object.fromEntries(adapters.map((a) => [a.name, a.status()]))
    const wa = platforms.whatsapp as Record<string, unknown> | undefined
    res.json({
      ok: adapters.some((a) => a.ready()),
      status: wa?.status ?? (adapters.some((a) => a.ready()) ? 'open' : 'starting'),
      bot: wa?.bot ?? {},
      platforms,
      brain_queue: brainQueueSize(),
      send_queues: pendingSends(),
      pairing_code: wa?.pairing_code ?? null,
    })
  })

  app.get('/qr', guard, async (_req, res) => {
    if (!state.qr) {
      res.status(404).send(state.status === 'open' ? 'already linked' : `no qr yet (status: ${state.status})`)
      return
    }
    res.type('png').send(await QRCode.toBuffer(state.qr, { width: 512 }))
  })

  app.get('/groups', guard, async (_req, res) => {
    const all = await Promise.all(adapters.map((a) => a.groups().catch(() => [])))
    res.json(all.flat())
  })

  app.post('/send', guard, (req, res) => {
    const job = validate(req.body)
    if (typeof job === 'string') {
      res.status(400).json({ error: job })
      return
    }
    if (!adapterFor(job.jid)?.ready()) {
      res.status(503).json({ error: 'not connected' })
      return
    }
    enqueue(job)
    res.status(202).json({ queued: true })
  })

  return app.listen(config.port, config.host, () => log.info(`gateway listening on http://${config.host}:${config.port}`))
}
