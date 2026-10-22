export type SendKind = 'text' | 'reaction' | 'voice' | 'image' | 'sticker'

export type SendJob = {
  ref?: string
  jid: string
  kind: SendKind
  text?: string
  emoji?: string
  media_b64?: string
  mimetype?: string
  quote?: { id: string; participant?: string | null; from_me?: boolean } | null
  delay_ms?: number
}

export type GroupInfo = { jid: string; subject: string; size: number | null; allowed: boolean; platform: string }

export interface Adapter {
  name: 'whatsapp' | 'telegram' | 'discord'
  owns(jid: string): boolean
  start(): Promise<void>
  ready(): boolean
  typing(jid: string, kind: SendKind, ms: number): Promise<void>
  deliver(job: SendJob): Promise<string | null>
  groups(): Promise<GroupInfo[]>
  status(): Record<string, unknown>
  stop(): Promise<void>
}
