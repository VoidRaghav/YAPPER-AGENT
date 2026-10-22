export type Vibe = { spice: number; chattiness: number; formality: number; emoji: number }

export type Features = { memes: boolean; voice: boolean; weekly_recap: boolean; reactions: boolean; trends: boolean }

export type Pack = {
  id: string
  name: string
  tagline: string
  color: string
  role: string
  persona: string
  vibe: Vibe
  language: string
  roasts: boolean
  speak_when: string[]
  quiet_when: string[]
  features: Features
  off_limits: string[]
  recap_style: string
  intro: string
  examples: { them: string; you: string }[]
}

const files = import.meta.glob<Pack>('../../../packs/*.json', { eager: true, import: 'default' })
const order = ['friends', 'family', 'study', 'work', 'community']
const rank = (id: string) => (order.includes(id) ? order.indexOf(id) : order.length)

export const packs = Object.entries(files)
  .filter(([path]) => !path.endsWith('.template.json'))
  .map(([, pack]) => pack)
  .sort((a, b) => rank(a.id) - rank(b.id) || a.name.localeCompare(b.name))

export const template = Object.entries(files).find(([path]) => path.endsWith('.template.json'))?.[1] ?? packs[0]

export const featureLabels: Record<keyof Features, string> = {
  memes: 'memes',
  voice: 'voice notes',
  weekly_recap: 'weekly awards',
  reactions: 'reactions',
  trends: 'trends',
}

export const vibeScale: { key: keyof Vibe; label: string; max: number }[] = [
  { key: 'spice', label: 'spice', max: 5 },
  { key: 'chattiness', label: 'chatty', max: 5 },
  { key: 'formality', label: 'formal', max: 5 },
  { key: 'emoji', label: 'emoji', max: 3 },
]

export const effectiveSpice = (pack: Pick<Pack, 'roasts' | 'vibe'>) => (pack.roasts ? pack.vibe.spice : Math.min(pack.vibe.spice, 2))

export const splitLine = (line: string) => {
  const match = line.match(/^([^:]{1,24}):\s*(.+)$/)
  return match ? { who: match[1], text: match[2] } : { who: null, text: line }
}
