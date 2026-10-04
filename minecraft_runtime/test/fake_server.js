'use strict'
/**
 * E2E 用的本地 Minecraft 服务器（仅测试）。
 *
 * 基于 flying-squid（Node 实现的真协议服务器，与 mineflayer 同属 prismarine
 * 生态）：真实的登录/生成/聊天广播/踢出流程，不是手搓包的假象。
 * 提供一个「观察者玩家」入口，模拟任务书 Test 4/6/7 里「另一个 Minecraft 客户端」。
 */

const mcServer = require('flying-squid')
const mineflayer = require('mineflayer')
const os = require('os')
const fs = require('fs')
const path = require('path')

function createFakeServer({ port, version = '1.16.4' } = {}) {
  const worldDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mc-fake-world-'))
  const server = mcServer.createMCServer({
    motd: 'CatooBot fake server',
    port,
    'max-players': 10,
    'online-mode': false,
    logging: false,
    gameMode: 1,
    difficulty: 0,
    worldFolder: worldDir,
    generation: { name: 'diamond_square', options: { worldHeight: 80 } },
    kickTimeout: 10000,
    plugins: {},
    modpe: false,
    'view-distance': 4,
    'player-list-text': { header: '', footer: '' },
    'everybody-op': true,
    'max-entities': 100,
    version,
  })

  return {
    server,
    /** 服务器视角：bot 是否已在玩家表里（Test 4 的进程内等价物）。 */
    playerByName: (name) =>
      Object.values(server.players).find((player) => player && player.username === name) ?? null,
    kick: (name, reason) => {
      const player = Object.values(server.players).find(
        (candidate) => candidate && candidate.username === name,
      )
      if (player) player.kick(reason)
      return player != null
    },
    /** Test 4/6/7 的「另一个 Minecraft 客户端」：真实第二个玩家。 */
    spawnObserver: (username) =>
      new Promise((resolve, reject) => {
        const observer = mineflayer.createBot({
          host: '127.0.0.1',
          port,
          username,
          hideErrors: true,
        })
        const seen = []
        const timer = setTimeout(() => {
          observer.end()
          reject(new Error(`observer ${username} did not spawn in time`))
        }, 30000)
        observer.once('spawn', () => {
          clearTimeout(timer)
          resolve({
            bot: observer,
            seen,
            chat: (message) => observer.chat(message),
            close: () => observer.end(),
          })
        })
        observer.on('message', (message) => {
          seen.push(typeof message?.toString === 'function' ? message.toString() : String(message))
        })
        observer.on('error', () => {
          /* 记录到 seen 之外；断言走超时路径 */
        })
      }),
    close: () =>
      new Promise((resolve) => {
        try {
          for (const player of Object.values(server.players)) {
            try {
              player.kick('E2E shutdown')
            } catch {
              /* already gone */
            }
          }
        } catch {
          /* ignore */
        }
        setTimeout(resolve, 800)
      }),
    cleanup: () => fs.rmSync(worldDir, { recursive: true, force: true }),
  }
}

module.exports = { createFakeServer }
