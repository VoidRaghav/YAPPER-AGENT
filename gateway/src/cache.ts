import type { GroupMetadata, WAMessage } from 'baileys'

class Lru<V> {
  private map = new Map<string, V>()
  private readonly capacity: number

  constructor(capacity: number) {
    this.capacity = capacity
  }

  get(key: string | null | undefined) {
    if (!key) return undefined
    const value = this.map.get(key)
    if (value !== undefined) {
      this.map.delete(key)
      this.map.set(key, value)
    }
    return value
  }

  set(key: string, value: V) {
    this.map.delete(key)
    this.map.set(key, value)
    if (this.map.size > this.capacity) this.map.delete(this.map.keys().next().value as string)
  }

  delete(key: string) {
    this.map.delete(key)
  }
}

export const messages = new Lru<WAMessage>(5000)
export const botSent = new Lru<true>(5000)

const groupTtlMs = 10 * 60 * 1000
const groups = new Map<string, { meta: GroupMetadata; at: number }>()

export const groupCache = {
  get(jid: string) {
    const hit = groups.get(jid)
    if (!hit || Date.now() - hit.at > groupTtlMs) return undefined
    return hit.meta
  },
  set(meta: GroupMetadata) {
    groups.set(meta.id, { meta, at: Date.now() })
  },
  patch(jid: string, patch: Partial<GroupMetadata>) {
    const hit = groups.get(jid)
    if (hit) groups.set(jid, { meta: { ...hit.meta, ...patch }, at: hit.at })
  },
  drop(jid: string) {
    groups.delete(jid)
  },
}
