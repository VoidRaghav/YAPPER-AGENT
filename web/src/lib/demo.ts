export type Line =
  | { who: string; text: string; tint?: string }
  | { bot: true; text: string; hud: string; react?: string }
  | { skip: number }

export type Scene = { pack: string; label: string; group: string; members: number; lines: Line[] }

export const scenes: Scene[] = [
  {
    pack: 'friends',
    label: 'Friends',
    group: 'goa plan (final final)',
    members: 9,
    lines: [
      { who: 'Aman', text: 'bhai 5 min mein pahunch raha hoon', tint: '#ffb38a' },
      { who: 'Priya', text: 'tu ghar se nikla bhi hai?', tint: '#7aa7ff' },
      { who: 'Aman', text: 'haan haan bas nikal raha', tint: '#ffb38a' },
      { bot: true, text: 'aman ki dictionary mein "nikal raha" matlab abhi naha raha hai. ETA 45 min', hud: 'reply · roast · overpromise spotted', react: '💀 4' },
      { who: 'Rahul', text: 'itna personal attack 😭', tint: '#ff7ad9' },
    ],
  },
  {
    pack: 'family',
    label: 'Family',
    group: 'Sharma Parivaar 🏡',
    members: 23,
    lines: [
      { who: 'Mama ji', text: '🌞🌺 Good Morning 🌺🌞', tint: '#c8f560' },
      { skip: 6 },
      { who: 'Didi', text: 'Nani ka birthday Sunday ko hai, cake kaun la raha hai?', tint: '#7aa7ff' },
      { bot: true, text: 'chocolate truffle. pichli baar 10 minute mein khatam ho gaya tha, mera vote wahi 🎂', hud: 'reply · warm · group question', react: '❤️ 6' },
      { who: 'Mummy', text: 'haan wahi lana', tint: '#ff7ad9' },
    ],
  },
  {
    pack: 'study',
    label: 'Study',
    group: 'DBMS last bench',
    members: 6,
    lines: [
      { who: 'Kabir', text: 'started the assignment (opened the pdf)', tint: '#ffb38a' },
      { bot: true, text: 'opening the pdf counts as attendance, not progress. 9 baje 1 hour sprint?', hud: 'reply · nudge · fake progress' },
      { who: 'Riya', text: "i'm in", tint: '#c8f560' },
      { who: 'Kabir', text: 'fine 😮‍💨', tint: '#ffb38a' },
    ],
  },
  {
    pack: 'work',
    label: 'Work',
    group: 'launch-squad',
    members: 11,
    lines: [
      { skip: 14 },
      { who: 'Neha', text: 'can someone recap what we decided on the launch?', tint: '#7aa7ff' },
      { bot: true, text: 'Launch moves to the 14th. Arjun owns the landing page, Sana owns QA. Nobody owns snacks, which feels like a risk.', hud: 'reply · recap · asked directly', react: '👍 3' },
      { who: 'Arjun', text: "I'll take snacks", tint: '#c8f560' },
    ],
  },
]
