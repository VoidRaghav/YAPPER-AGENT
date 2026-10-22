import pino from 'pino'
import { config } from './config.ts'

export const log = pino({ level: config.logLevel, base: { svc: 'gateway' } })
export const waLog = pino({ level: config.waLogLevel, base: { svc: 'baileys' } })
