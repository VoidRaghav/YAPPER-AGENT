import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { WAMessage } from 'baileys'

process.env.SHARED_NUMBER = 'true'
process.env.SHARED_BOT_PREFIX = '🤖 '
process.env.OFFLINE_CATCHUP_MINUTES = '360'

const { catchUp, fromBot } = await import('../src/socket.ts')

const now = 1_800_000_000
const msg = (ageSeconds: number, fromMe = false, text = 'yapper trip kab hai') =>
  ({ key: { id: 'X1', remoteJid: '1203@g.us', fromMe }, messageTimestamp: now - ageSeconds, message: { conversation: text } }) as unknown as WAMessage

test('messages delivered while the gateway was offline are caught up within the window', () => {
  assert.equal(catchUp(msg(30), now), true)
  assert.equal(catchUp(msg(5 * 3600), now), true)
  assert.equal(catchUp(msg(7 * 3600), now), false)
})

test('owner messages typed from the phone count in shared mode', () => {
  assert.equal(catchUp(msg(60, true), now), true)
  assert.equal(fromBot(msg(60, true, 'kal milte hai')), false)
})

test('the bot never mistakes its own labelled messages for the owner after a restart', () => {
  assert.equal(fromBot(msg(60, true, '🤖 goa plan part 7 announced')), true)
  assert.equal(fromBot(msg(60, false, '🤖 copied by a friend')), false)
})
