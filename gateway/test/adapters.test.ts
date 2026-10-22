import assert from 'node:assert/strict'
import { test } from 'node:test'
import { Collection } from 'discord.js'
import type { Message as DiscordMessage } from 'discord.js'
import type { Message, UserFromGetMe } from 'grammy/types'

process.env.ALLOWED_GROUP_JIDS = 'tg:-1001,dc:555'
process.env.WHATSAPP_ENABLED = 'false'

const { toEvent: telegramEvent, reactionFor } = await import('../src/adapters/telegram.ts')
const { toEvent: discordEvent } = await import('../src/adapters/discord.ts')
const { validate } = await import('../src/send.ts')

const self = { id: 99, is_bot: true, first_name: 'Yapper', username: 'yapper_bot', can_read_all_group_messages: true } as UserFromGetMe

const tg = (extra: Record<string, unknown>) =>
  ({ message_id: 7, date: 1_800_000_000, chat: { id: -1001, type: 'supergroup', title: 'Goa 26' }, from: { id: 42, is_bot: false, first_name: 'Aman', last_name: 'K' }, ...extra }) as unknown as Message

test('telegram message becomes a brain event with a globally unique id', async () => {
  const ev = await telegramEvent(tg({ text: 'guys goa final this time' }), self)
  assert.ok(ev)
  assert.equal(ev.group_jid, 'tg:-1001')
  assert.equal(ev.id, 'tg:-1001:7')
  assert.equal(ev.sender_jid, 'tg:42')
  assert.equal(ev.sender_name, 'Aman K')
  assert.equal(ev.mentioned_bot, false)
})

test('telegram @mention and reply to the bot both count as calling it', async () => {
  const mention = await telegramEvent(tg({ text: '@yapper_bot kya scene', entities: [{ type: 'mention', offset: 0, length: 11 }] }), self)
  assert.equal(mention?.mentioned_bot, true)
  const reply = await telegramEvent(tg({ text: 'lol', reply_to_message: { message_id: 5, date: 0, chat: { id: -1001, type: 'supergroup' }, from: { id: 99, is_bot: true, first_name: 'Yapper' }, text: 'goa part 7' } }), self)
  assert.equal(reply?.mentioned_bot, true)
  assert.deepEqual(reply?.quoted, { id: 'tg:-1001:5', participant: 'tg:99', from_bot: true, text: 'goa part 7' })
})

test('telegram ignores other groups, private chats and its own messages', async () => {
  assert.equal(await telegramEvent(tg({ text: 'hi', chat: { id: -2002, type: 'supergroup', title: 'Other' } }), self), null)
  assert.equal(await telegramEvent(tg({ text: 'hi', chat: { id: 42, type: 'private', first_name: 'Aman' } }), self), null)
  assert.equal(await telegramEvent(tg({ text: 'hi', from: { id: 99, is_bot: true, first_name: 'Yapper' } }), self), null)
})

test('telegram reactions map to emojis Telegram accepts', () => {
  assert.equal(reactionFor('😂'), '🤣')
  assert.equal(reactionFor('❤️'), '❤')
  assert.equal(reactionFor('🔥'), '🔥')
  assert.equal(reactionFor('🦖'), '👍')
})

const dc = (extra: Record<string, unknown>) =>
  ({
    inGuild: () => true,
    author: { bot: false, id: '11', username: 'priya', globalName: 'Priya' },
    member: { displayName: 'Priya S' },
    guild: { name: 'Study Hall', memberCount: 12 },
    channel: { name: 'dbms' },
    channelId: '555',
    id: '777',
    content: 'started the assignment',
    cleanContent: 'started the assignment',
    attachments: new Collection(),
    stickers: new Collection(),
    mentions: { users: new Collection() },
    flags: { has: () => false },
    reference: null,
    createdTimestamp: 1_800_000_000_000,
    ...extra,
  }) as unknown as DiscordMessage

test('discord message becomes a brain event', async () => {
  const ev = await discordEvent(dc({}), '99')
  assert.ok(ev)
  assert.equal(ev.group_jid, 'dc:555')
  assert.equal(ev.id, 'dc:555:777')
  assert.equal(ev.sender_name, 'Priya S')
  assert.equal(ev.group_name, 'Study Hall #dbms')
  assert.equal(ev.ts, 1_800_000_000)
})

test('discord mentions of the bot call it, other mentions pass through', async () => {
  const users = new Collection<string, unknown>([['99', {}], ['12', {}]])
  const ev = await discordEvent(dc({ mentions: { users } }), '99')
  assert.equal(ev?.mentioned_bot, true)
  assert.deepEqual(ev?.mentions, ['dc:12'])
})

test('discord ignores bots, DMs and channels that are not allowed', async () => {
  assert.equal(await discordEvent(dc({ author: { bot: true, id: '5', username: 'x' } }), '99'), null)
  assert.equal(await discordEvent(dc({ inGuild: () => false }), '99'), null)
  assert.equal(await discordEvent(dc({ channelId: '999' }), '99'), null)
})

test('send jobs are only accepted for platforms that are switched on', () => {
  assert.equal(validate({ jid: '120363@g.us', kind: 'text', text: 'hi' }), 'jid must belong to a connected platform')
  assert.equal(validate({ jid: 'tg:-1001', kind: 'text', text: 'hi' }), 'jid must belong to a connected platform')
})
