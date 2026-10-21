const path = require('path')
const dir = process.argv[2]
const host = process.argv[3] || '127.0.0.1'
const port = Number(process.argv[4] || 25565)
const target = process.argv[5] || 'Catodayo'
const mineflayer = require(path.join(dir, 'node_modules', 'mineflayer'))
const bot = mineflayer.createBot({ host, port, username: 'IdObserveR1', auth: 'offline', version: '1.21.1', viewDistance: 'normal' })
bot.on('error', (e) => console.log('[obs] error ' + String(e && e.message).slice(0, 120)))
bot.once('spawn', async () => {
  console.log('[obs] joined ' + bot.username)
  for (let i = 0; i < 200; i++) {
    const player = bot.players && bot.players[target]
    const entity = player && player.entity
    if (entity && entity.position) {
      const p = entity.position
      console.log('[obs] ' + JSON.stringify({ observer: bot.username, target, x: p.x, y: p.y, z: p.z, dimension: bot.game.dimension }))
      bot.quit(); process.exit(0)
    }
    await new Promise((r) => setTimeout(r, 250))
  }
  console.log('[obs] target_not_visible')
  bot.quit(); process.exit(1)
})
